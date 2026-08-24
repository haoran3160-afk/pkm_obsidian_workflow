use std::ffi::OsStr;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::{AsRawHandle, FromRawHandle};
use std::path::{Component, Path, PathBuf};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use windows_sys::Win32::Foundation::{
    CloseHandle, GetHandleInformation, GetLastError, LocalFree, SetHandleInformation,
    ERROR_ACCESS_DENIED, ERROR_BAD_EXE_FORMAT, ERROR_ELEVATION_REQUIRED, ERROR_INSUFFICIENT_BUFFER,
    ERROR_INVALID_HANDLE, ERROR_INVALID_PARAMETER, ERROR_NOT_SUPPORTED, HANDLE,
    HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE, WAIT_ABANDONED, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
use windows_sys::Win32::Security::Cryptography::{
    BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG,
};
use windows_sys::Win32::Security::Isolation::GetAppContainerFolderPath;
use windows_sys::Win32::Security::{
    GetTokenInformation, IsValidSid, TokenAppContainerSid, TokenCapabilities, TokenIsAppContainer,
    TokenIsLessPrivilegedAppContainer, SECURITY_ATTRIBUTES, SECURITY_CAPABILITIES,
    TOKEN_APPCONTAINER_INFORMATION, TOKEN_QUERY, TOKEN_QUERY_SOURCE,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, FlushFileBuffers, FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES, FILE_SHARE_READ, FILE_SHARE_WRITE,
    OPEN_EXISTING,
};
use windows_sys::Win32::System::Com::CoTaskMemFree;
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, JobObjectBasicAccountingInformation, QueryInformationJobObject,
    TerminateJobObject, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
};
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::SystemServices::PROCESS_MITIGATION_CHILD_PROCESS_POLICY;
use windows_sys::Win32::System::Threading::{
    CreateMutexW, CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess,
    GetProcessMitigationPolicy, InitializeProcThreadAttributeList, OpenProcessToken,
    ProcessChildProcessPolicy, ReleaseMutex, ResumeThread, TerminateProcess,
    UpdateProcThreadAttribute, WaitForSingleObject, CREATE_NO_WINDOW, CREATE_SUSPENDED,
    CREATE_UNICODE_ENVIRONMENT, DETACHED_PROCESS, EXTENDED_STARTUPINFO_PRESENT,
    LPPROC_THREAD_ATTRIBUTE_LIST, PROCESS_INFORMATION,
    PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY,
    PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
    PROC_THREAD_ATTRIBUTE_JOB_LIST, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
    STARTF_USESTDHANDLES, STARTUPINFOEXW,
};
use windows_sys::Win32::System::WindowsProgramming::{
    PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT, PROCESS_CREATION_CHILD_PROCESS_RESTRICTED,
};

use super::{
    appcontainer_sid_bytes, appcontainer_sids_equal, create_qualified_job,
    create_zero_capability_profile, delete_profile, derive_appcontainer_sid, file_id_from_handle,
    install_wfp_default_deny, native_preflight_contract, process_is_elevated, sha256_open_file,
    verify_authenticode, verify_pe_file, verify_wfp_dynamic_cleanup, wide_null, OwnedKernelHandle,
    PREFLIGHT_DEADLINE,
};
use crate::{
    Blocker, FileCreationAuthority, Gate, NtfsFileIdentity, EXPECTED_CODEX_EXE_SHA256,
    EXPECTED_CODEX_VERSION, MAX_STDERR_BYTES, MAX_STDOUT_BYTES,
};

mod helper;
pub use helper::{
    run_native_helper_diagnostic, NativeHelperDebugEvent, NativeHelperDiagnosticResult,
    NativeHelperDiagnosticVariantResult, NativeHelperHomeChangeEvent, NativeHelperHomeEntry,
    NativeHelperJobEvent, NativeHelperProcessEvidence,
};

#[link(name = "ntdll")]
extern "system" {
    fn NtQueryInformationToken(
        token_handle: HANDLE,
        token_information_class: windows_sys::Win32::Security::TOKEN_INFORMATION_CLASS,
        token_information: *mut core::ffi::c_void,
        token_information_length: u32,
        return_length: *mut u32,
    ) -> i32;
}

const PREFLIGHT_SCRATCH_ROOT: &str = r"D:\personal\obsidian_workflow-runtime\scratch";
const PREFLIGHT_RUN_PREFIX: &str = "codex-t13b-preflight-";
const CHILD_POLICY_RUN_PREFIX: &str = "codex-t13b-child-policy-";
const APPCONTAINER_PROFILE_RUN_PREFIX: &str = "codex-t13b-appcontainer-profile-";
const LPAC_TOKEN_RUN_PREFIX: &str = "codex-t13b-lpac-token-";
const WFP_POLICY_RUN_PREFIX: &str = "codex-t13b-wfp-policy-";
const DIAGNOSTIC_RUN_PREFIXES: [&str; 4] = [
    "codex-t13b-diagnostic-a-",
    "codex-t13b-diagnostic-b-",
    "codex-t13b-diagnostic-c-",
    "codex-t13b-diagnostic-d-",
];
const DIAGNOSTIC_SUMMARY_PREFIX: &str = "codex-t13b-diagnostic-summary-";
const PREFLIGHT_MUTEX_NAME: &str = r"Global\OpenAI.PKMWorkflow.Codex.T13b.Preflight";
const PREFLIGHT_TERMINATE_EXIT: u32 = 0xc000_013a;
const STATUS_DLL_INIT_FAILED: u32 = 0xc000_0142;
const PREFLIGHT_CLEANUP_WAIT: Duration = Duration::from_secs(10);
const PREFLIGHT_STDIN: &[u8] = b"";
const PREFLIGHT_PROMPT: &[u8] = b"PREFLIGHT_ONLY\n";
const PREFLIGHT_SCHEMA: &[u8] = b"{}\n";
const DIAGNOSTIC_VARIANT_DEADLINE: Duration = Duration::from_secs(10);
const CHILD_POLICY_CANARY_DEADLINE: Duration = Duration::from_secs(10);
const EXPECTED_CHILD_POLICY_CANARY_STDOUT: &str = "{\"terminal\":\"CHILD_CREATION_BLOCKED_BY_POLICY\",\"child_creation_attempted\":true,\"child_creation_blocked\":true,\"win32_error\":367}\n";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LaunchPolicy {
    id: &'static str,
    atomic_job_list: bool,
    child_process_restricted: bool,
    assigned_after_create: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ConsoleCreationMode {
    CreateNoWindow,
    DetachedProcess,
}

impl ConsoleCreationMode {
    fn flag(self) -> u32 {
        match self {
            Self::CreateNoWindow => CREATE_NO_WINDOW,
            Self::DetachedProcess => DETACHED_PROCESS,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::CreateNoWindow => "CREATE_NO_WINDOW",
            Self::DetachedProcess => "DETACHED_PROCESS",
        }
    }
}

const CANONICAL_PREFLIGHT_POLICY: LaunchPolicy = LaunchPolicy {
    id: "A",
    atomic_job_list: true,
    child_process_restricted: true,
    assigned_after_create: false,
};

const DIAGNOSTIC_POLICIES: [LaunchPolicy; 4] = [
    CANONICAL_PREFLIGHT_POLICY,
    LaunchPolicy {
        id: "B",
        atomic_job_list: true,
        child_process_restricted: false,
        assigned_after_create: false,
    },
    LaunchPolicy {
        id: "C",
        atomic_job_list: false,
        child_process_restricted: true,
        assigned_after_create: true,
    },
    LaunchPolicy {
        id: "D",
        atomic_job_list: false,
        child_process_restricted: false,
        assigned_after_create: true,
    },
];

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativePreflightResult {
    pub exit_code: u8,
    pub terminal: &'static str,
    pub blocker: Option<&'static str>,
    pub run_root: Option<PathBuf>,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
    pub child_created: bool,
    pub child_resumed: bool,
    pub descendants_zero: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeChildPolicyDiagnosticResult {
    pub exit_code: u8,
    pub terminal: &'static str,
    pub blocker: &'static str,
    pub production: bool,
    pub live: bool,
    pub run_root: Option<PathBuf>,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
    pub child_process_policy_readback: bool,
    pub child_process_policy_flags: Option<u32>,
    pub canary_child_creation_attempted: bool,
    pub canary_child_creation_blocked: bool,
    pub job_total_processes: Option<u32>,
    pub job_active_processes: Option<u32>,
    pub descendants_zero: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeAppContainerProfileDiagnosticResult {
    pub exit_code: u8,
    pub terminal: &'static str,
    pub blocker: &'static str,
    pub production: bool,
    pub live: bool,
    pub run_root: Option<PathBuf>,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
    pub profile_name_sha256: Option<String>,
    pub package_sid_sha256: Option<String>,
    pub zero_capabilities: bool,
    pub first_create: bool,
    pub derived_sid_match: bool,
    pub first_delete: bool,
    pub recreate: bool,
    pub sid_stable: bool,
    pub final_delete: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeWfpPolicyDiagnosticResult {
    pub exit_code: u8,
    pub terminal: &'static str,
    pub blocker: &'static str,
    pub production: bool,
    pub live: bool,
    pub run_root: Option<PathBuf>,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
    pub profile_created: bool,
    pub wfp_installed: bool,
    pub filter_readback: bool,
    pub cleanup_verified: bool,
    pub negative_matrix: bool,
    pub package_sid_sha256: Option<String>,
    pub wfp_policy_sha256: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeLpacTokenDiagnosticResult {
    pub exit_code: u8,
    pub terminal: &'static str,
    pub blocker: &'static str,
    pub production: bool,
    pub live: bool,
    pub run_root: Option<PathBuf>,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
    pub package_sid_sha256: Option<String>,
    pub canary_sha256: Option<String>,
    pub profile_created: bool,
    pub child_created_suspended: bool,
    pub create_win32_error: Option<u32>,
    pub child_resumed: bool,
    pub lpac_attribute_accepted: bool,
    pub job_membership_verified: bool,
    pub child_process_restricted: bool,
    pub token_is_appcontainer: bool,
    pub token_is_lpac: Option<bool>,
    pub lpac_direct_query_supported: bool,
    pub capability_count: Option<u32>,
    pub package_sid_match: bool,
    pub descendants_zero: bool,
    pub profile_deleted: bool,
}

impl NativeLpacTokenDiagnosticResult {
    pub fn to_json(&self) -> String {
        format!(
            "{{\"terminal\":\"{}\",\"blocker\":\"{}\",\"production\":{},\"live\":{},\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"profile_created\":{},\"child_created_suspended\":{},\"create_win32_error\":{},\"child_resumed\":{},\"lpac_attribute_accepted\":{},\"job_membership_verified\":{},\"child_process_restricted\":{},\"token_is_appcontainer\":{},\"token_is_lpac\":{},\"lpac_direct_query_supported\":{},\"capability_count\":{},\"package_sid_match\":{},\"descendants_zero\":{},\"profile_deleted\":{},\"package_sid_sha256\":{},\"canary_sha256\":{},\"run_root\":{},\"receipt_path\":{},\"receipt_sha256\":{}}}",
            json_escape(self.terminal),
            json_escape(self.blocker),
            json_bool(self.production),
            json_bool(self.live),
            json_bool(self.profile_created),
            json_bool(self.child_created_suspended),
            json_u32_or_null(self.create_win32_error),
            json_bool(self.child_resumed),
            json_bool(self.lpac_attribute_accepted),
            json_bool(self.job_membership_verified),
            json_bool(self.child_process_restricted),
            json_bool(self.token_is_appcontainer),
            json_bool_or_null(self.token_is_lpac),
            json_bool(self.lpac_direct_query_supported),
            json_u32_or_null(self.capability_count),
            json_bool(self.package_sid_match),
            json_bool(self.descendants_zero),
            json_bool(self.profile_deleted),
            json_string_or_null(self.package_sid_sha256.as_deref()),
            json_string_or_null(self.canary_sha256.as_deref()),
            json_path_or_null(self.run_root.as_deref()),
            json_path_or_null(self.receipt_path.as_deref()),
            json_string_or_null(self.receipt_sha256.as_deref()),
        )
    }
}

impl NativeWfpPolicyDiagnosticResult {
    pub fn to_json(&self) -> String {
        format!(
            "{{\"terminal\":\"{}\",\"blocker\":\"{}\",\"production\":{},\"live\":{},\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"profile_created\":{},\"wfp_installed\":{},\"filter_readback\":{},\"cleanup_verified\":{},\"negative_matrix\":{},\"package_sid_sha256\":{},\"wfp_policy_sha256\":{},\"run_root\":{},\"receipt_path\":{},\"receipt_sha256\":{}}}",
            json_escape(self.terminal),
            json_escape(self.blocker),
            json_bool(self.production),
            json_bool(self.live),
            json_bool(self.profile_created),
            json_bool(self.wfp_installed),
            json_bool(self.filter_readback),
            json_bool(self.cleanup_verified),
            json_bool(self.negative_matrix),
            json_string_or_null(self.package_sid_sha256.as_deref()),
            json_string_or_null(self.wfp_policy_sha256.as_deref()),
            json_path_or_null(self.run_root.as_deref()),
            json_path_or_null(self.receipt_path.as_deref()),
            json_string_or_null(self.receipt_sha256.as_deref()),
        )
    }
}

impl NativeAppContainerProfileDiagnosticResult {
    pub fn to_json(&self) -> String {
        format!(
            "{{\"terminal\":\"{}\",\"blocker\":\"{}\",\"production\":{},\"live\":{},\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"profile_name_sha256\":{},\"package_sid_sha256\":{},\"zero_capabilities\":{},\"first_create\":{},\"derived_sid_match\":{},\"first_delete\":{},\"recreate\":{},\"sid_stable\":{},\"final_delete\":{},\"run_root\":{},\"receipt_path\":{},\"receipt_sha256\":{}}}",
            json_escape(self.terminal),
            json_escape(self.blocker),
            json_bool(self.production),
            json_bool(self.live),
            json_string_or_null(self.profile_name_sha256.as_deref()),
            json_string_or_null(self.package_sid_sha256.as_deref()),
            json_bool(self.zero_capabilities),
            json_bool(self.first_create),
            json_bool(self.derived_sid_match),
            json_bool(self.first_delete),
            json_bool(self.recreate),
            json_bool(self.sid_stable),
            json_bool(self.final_delete),
            json_path_or_null(self.run_root.as_deref()),
            json_path_or_null(self.receipt_path.as_deref()),
            json_string_or_null(self.receipt_sha256.as_deref()),
        )
    }
}

impl NativeChildPolicyDiagnosticResult {
    pub fn to_json(&self) -> String {
        format!(
            "{{\"terminal\":\"{}\",\"blocker\":\"{}\",\"production\":{},\"live\":{},\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"network_usage\":\"UNOBSERVED\",\"credential_access\":\"UNOBSERVED\",\"console_creation_mode\":\"DETACHED_PROCESS\",\"child_process_restricted\":true,\"child_process_policy_readback\":{},\"child_process_policy_flags\":{},\"canary_child_creation_attempted\":{},\"canary_child_creation_blocked\":{},\"job_total_processes\":{},\"job_active_processes\":{},\"descendants_zero\":{},\"run_root\":{},\"receipt_path\":{},\"receipt_sha256\":{}}}",
            json_escape(self.terminal),
            json_escape(self.blocker),
            json_bool(self.production),
            json_bool(self.live),
            json_bool(self.child_process_policy_readback),
            json_u32_or_null(self.child_process_policy_flags),
            json_bool(self.canary_child_creation_attempted),
            json_bool(self.canary_child_creation_blocked),
            json_u32_or_null(self.job_total_processes),
            json_u32_or_null(self.job_active_processes),
            json_bool(self.descendants_zero),
            json_path_or_null(self.run_root.as_deref()),
            json_path_or_null(self.receipt_path.as_deref()),
            json_string_or_null(self.receipt_sha256.as_deref()),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativePreflightDiagnosticVariantResult {
    pub id: &'static str,
    pub terminal: &'static str,
    pub blocker: &'static str,
    pub production: bool,
    pub live: bool,
    pub auth_isolation: bool,
    pub network_enforcement: bool,
    pub network_usage: &'static str,
    pub credential_access: &'static str,
    pub atomic_job_list: bool,
    pub child_process_restricted: bool,
    pub assigned_after_create: bool,
    pub run_root: Option<PathBuf>,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
    pub child_created: bool,
    pub child_resumed: bool,
    pub descendants_zero: bool,
    pub exit_code: Option<u32>,
    pub stdout_sha256: Option<String>,
    pub stderr_sha256: Option<String>,
    pub stdout_total_bytes: usize,
    pub stderr_total_bytes: usize,
    pub stdout_overflow: bool,
    pub stderr_overflow: bool,
    pub pre_resume_total_processes: Option<u32>,
    pub pre_resume_active_processes: Option<u32>,
    pub final_total_processes: Option<u32>,
    pub final_active_processes: Option<u32>,
    pub max_observed_total_processes: Option<u32>,
    pub max_observed_active_processes: Option<u32>,
    pub max_additional_job_processes_seen: Option<u32>,
}

impl NativePreflightDiagnosticVariantResult {
    fn to_json(&self) -> String {
        format!(
            "{{\"id\":\"{}\",\"terminal\":\"{}\",\"blocker\":\"{}\",\"production\":{},\"live\":{},\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"auth_isolation\":{},\"network_enforcement\":{},\"network_usage\":\"{}\",\"credential_access\":\"{}\",\"atomic_job_list\":{},\"child_process_restricted\":{},\"assigned_after_create\":{},\"run_root\":{},\"receipt_path\":{},\"receipt_sha256\":{},\"child_created\":{},\"child_resumed\":{},\"descendants_zero\":{},\"exit_code\":{},\"stdout_sha256\":{},\"stderr_sha256\":{},\"stdout_total_bytes\":{},\"stderr_total_bytes\":{},\"stdout_overflow\":{},\"stderr_overflow\":{},\"pre_resume_total_processes\":{},\"pre_resume_active_processes\":{},\"final_total_processes\":{},\"final_active_processes\":{},\"max_observed_total_processes\":{},\"max_observed_active_processes\":{},\"max_additional_job_processes_seen\":{}}}",
            self.id,
            json_escape(self.terminal),
            json_escape(self.blocker),
            json_bool(self.production),
            json_bool(self.live),
            json_bool(self.auth_isolation),
            json_bool(self.network_enforcement),
            json_escape(self.network_usage),
            json_escape(self.credential_access),
            json_bool(self.atomic_job_list),
            json_bool(self.child_process_restricted),
            json_bool(self.assigned_after_create),
            json_path_or_null(self.run_root.as_deref()),
            json_path_or_null(self.receipt_path.as_deref()),
            json_string_or_null(self.receipt_sha256.as_deref()),
            json_bool(self.child_created),
            json_bool(self.child_resumed),
            json_bool(self.descendants_zero),
            json_u32_or_null(self.exit_code),
            json_string_or_null(self.stdout_sha256.as_deref()),
            json_string_or_null(self.stderr_sha256.as_deref()),
            self.stdout_total_bytes,
            self.stderr_total_bytes,
            json_bool(self.stdout_overflow),
            json_bool(self.stderr_overflow),
            json_u32_or_null(self.pre_resume_total_processes),
            json_u32_or_null(self.pre_resume_active_processes),
            json_u32_or_null(self.final_total_processes),
            json_u32_or_null(self.final_active_processes),
            json_u32_or_null(self.max_observed_total_processes),
            json_u32_or_null(self.max_observed_active_processes),
            json_u32_or_null(self.max_additional_job_processes_seen),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativePreflightDiagnosticResult {
    pub exit_code: u8,
    pub terminal: &'static str,
    pub blocker: &'static str,
    pub production: bool,
    pub live: bool,
    pub auth_isolation: bool,
    pub network_enforcement: bool,
    pub network_usage: &'static str,
    pub credential_access: &'static str,
    pub finding: &'static str,
    pub decision_required: bool,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
    pub variants: Vec<NativePreflightDiagnosticVariantResult>,
}

impl NativePreflightDiagnosticResult {
    pub fn to_json(&self) -> String {
        let variants = self
            .variants
            .iter()
            .map(NativePreflightDiagnosticVariantResult::to_json)
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{{\"terminal\":\"{}\",\"blocker\":\"{}\",\"production\":{},\"live\":{},\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"auth_isolation\":{},\"network_enforcement\":{},\"network_usage\":\"{}\",\"credential_access\":\"{}\",\"finding\":\"{}\",\"decision_required\":{},\"receipt_path\":{},\"receipt_sha256\":{},\"variants\":[{}]}}",
            json_escape(self.terminal),
            json_escape(self.blocker),
            json_bool(self.production),
            json_bool(self.live),
            json_bool(self.auth_isolation),
            json_bool(self.network_enforcement),
            json_escape(self.network_usage),
            json_escape(self.credential_access),
            json_escape(self.finding),
            json_bool(self.decision_required),
            json_path_or_null(self.receipt_path.as_deref()),
            json_string_or_null(self.receipt_sha256.as_deref()),
            variants,
        )
    }
}

pub fn run_native_preflight_diagnostic() -> NativePreflightDiagnosticResult {
    run_bounded_diagnostic_suite()
}

impl NativePreflightResult {
    pub fn to_json(&self) -> String {
        let blocker = self
            .blocker
            .map(|value| format!("\"{}\"", json_escape(value)))
            .unwrap_or_else(|| "null".to_owned());
        let run_root = json_path_or_null(self.run_root.as_deref());
        let receipt_path = json_path_or_null(self.receipt_path.as_deref());
        let receipt_sha256 = self
            .receipt_sha256
            .as_deref()
            .map(|value| format!("\"{}\"", json_escape(value)))
            .unwrap_or_else(|| "null".to_owned());
        format!(
            "{{\"terminal\":\"{}\",\"blocker\":{},\"mode\":\"PREFLIGHT_ONLY\",\"live\":false,\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"auth_isolation\":false,\"network_enforcement\":false,\"network_usage\":\"UNOBSERVED\",\"credential_access\":\"UNOBSERVED\",\"run_root\":{},\"receipt_path\":{},\"receipt_sha256\":{},\"child_created\":{},\"child_resumed\":{},\"descendants_zero\":{}}}",
            self.terminal,
            blocker,
            run_root,
            receipt_path,
            receipt_sha256,
            json_bool(self.child_created),
            json_bool(self.child_resumed),
            json_bool(self.descendants_zero),
        )
    }
}

#[derive(Debug)]
struct HeldDirectoryChain {
    handles: Vec<File>,
    volume_serial: u64,
}

impl HeldDirectoryChain {
    fn open(path: &Path, gate: Gate) -> Result<Self, Blocker> {
        if !path.is_absolute()
            || path
                .components()
                .any(|part| matches!(part, Component::CurDir | Component::ParentDir))
        {
            return Err(Blocker::new(gate, "ANCESTOR_PATH_INVALID"));
        }
        let mut cursor = PathBuf::new();
        let mut handles = Vec::new();
        let mut volume_serial = None;
        for component in path.components() {
            match component {
                Component::Prefix(prefix) => cursor.push(prefix.as_os_str()),
                Component::RootDir => {
                    cursor.push(Path::new(r"\"));
                    let directory = open_directory(&cursor, gate)?;
                    let (volume, _, _, _) = file_id_from_handle(&directory, gate)?;
                    volume_serial = Some(volume);
                    handles.push(directory);
                }
                Component::Normal(part) => {
                    cursor.push(part);
                    let directory = open_directory(&cursor, gate)?;
                    let (volume, _, _, _) = file_id_from_handle(&directory, gate)?;
                    if volume_serial != Some(volume) {
                        return Err(Blocker::new(gate, "ANCESTOR_VOLUME_CHANGED"));
                    }
                    handles.push(directory);
                }
                Component::CurDir | Component::ParentDir => {
                    return Err(Blocker::new(gate, "ANCESTOR_PATH_INVALID"));
                }
            }
        }
        if handles.is_empty() {
            return Err(Blocker::new(gate, "ANCESTOR_CHAIN_EMPTY"));
        }
        Ok(Self {
            handles,
            volume_serial: volume_serial
                .ok_or_else(|| Blocker::new(gate, "ANCESTOR_VOLUME_MISSING"))?,
        })
    }

    fn last(&self, gate: Gate) -> Result<&File, Blocker> {
        self.handles
            .last()
            .ok_or_else(|| Blocker::new(gate, "ANCESTOR_CHAIN_EMPTY"))
    }

    fn push_created_directory(&mut self, path: &Path, gate: Gate) -> Result<(), Blocker> {
        let directory = open_directory(path, gate)?;
        let (volume, _, _, _) = file_id_from_handle(&directory, gate)?;
        if volume != self.volume_serial {
            return Err(Blocker::new(gate, "RUNTIME_VOLUME_CHANGED"));
        }
        self.handles.push(directory);
        Ok(())
    }
}

fn open_directory(path: &Path, gate: Gate) -> Result<File, Blocker> {
    let wide = wide_null(path.as_os_str());
    // SAFETY: wide is NUL-terminated. No share-delete permission is granted,
    // so every opened component remains pinned for the lifetime of the File.
    let handle = unsafe {
        CreateFileW(
            wide.as_ptr(),
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err(Blocker::new(gate, "ANCESTOR_DIRECTORY_OPEN_FAILED"));
    }
    // SAFETY: ownership of this unique CreateFileW handle is transferred once.
    let directory = unsafe { File::from_raw_handle(handle.cast()) };
    let attributes = directory
        .metadata()
        .map_err(|_| Blocker::new(gate, "ANCESTOR_DIRECTORY_METADATA_FAILED"))?
        .file_attributes();
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(Blocker::new(gate, "ANCESTOR_REPARSE_POINT_FORBIDDEN"));
    }
    Ok(directory)
}

#[derive(Debug)]
struct RuntimeAuthority {
    run_id: String,
    run_root: PathBuf,
    codex_home: PathBuf,
    temp: PathBuf,
    sealed: PathBuf,
    output: PathBuf,
    _chain: HeldDirectoryChain,
    run_root_directory: File,
    sealed_directory: File,
    output_directory: File,
}

impl RuntimeAuthority {
    fn create() -> Result<Self, Blocker> {
        Self::create_with_prefix(PREFLIGHT_RUN_PREFIX)
    }

    fn create_diagnostic(variant_index: usize) -> Result<Self, Blocker> {
        let prefix = DIAGNOSTIC_RUN_PREFIXES
            .get(variant_index)
            .copied()
            .ok_or_else(|| Blocker::new(Gate::Manifest, "DIAGNOSTIC_VARIANT_INVALID"))?;
        Self::create_with_prefix(prefix)
    }

    fn create_diagnostic_summary() -> Result<Self, Blocker> {
        Self::create_with_prefix(DIAGNOSTIC_SUMMARY_PREFIX)
    }

    fn create_with_prefix(prefix: &'static str) -> Result<Self, Blocker> {
        let scratch = PathBuf::from(PREFLIGHT_SCRATCH_ROOT);
        let mut chain = HeldDirectoryChain::open(&scratch, Gate::Manifest)?;
        let mut selected = None;
        for _ in 0..4 {
            let run_id = random_run_id()?;
            let run_root = scratch.join(format!("{prefix}{run_id}"));
            match std::fs::create_dir(&run_root) {
                Ok(()) => {
                    selected = Some((run_id, run_root));
                    break;
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(_) => {
                    return Err(Blocker::new(
                        Gate::Manifest,
                        "RUNTIME_ROOT_CREATE_NEW_FAILED",
                    ));
                }
            }
        }
        let (run_id, run_root) =
            selected.ok_or_else(|| Blocker::new(Gate::Manifest, "RUNTIME_RUN_ID_COLLISION"))?;
        chain.push_created_directory(&run_root, Gate::Manifest)?;

        let codex_home = run_root.join("empty-home");
        let temp = run_root.join("temp");
        let sealed = run_root.join("sealed");
        let output = run_root.join("output");
        for directory in [&codex_home, &temp, &sealed, &output] {
            std::fs::create_dir(directory)
                .map_err(|_| Blocker::new(Gate::Manifest, "RUNTIME_DIRECTORY_CREATE_NEW_FAILED"))?;
            chain.push_created_directory(directory, Gate::Manifest)?;
        }
        let run_root_directory = open_directory(&run_root, Gate::Manifest)?;
        let sealed_directory = open_directory(&sealed, Gate::Manifest)?;
        let output_directory = open_directory(&output, Gate::OutputAndDeadline)?;
        Ok(Self {
            run_id,
            run_root,
            codex_home,
            temp,
            sealed,
            output,
            _chain: chain,
            run_root_directory,
            sealed_directory,
            output_directory,
        })
    }
}

fn random_run_id() -> Result<String, Blocker> {
    let mut random = [0u8; 16];
    // SAFETY: a null algorithm handle plus SYSTEM_PREFERRED_RNG is the
    // documented system RNG form; random is an exact writable buffer.
    if unsafe {
        BCryptGenRandom(
            std::ptr::null_mut(),
            random.as_mut_ptr(),
            random.len() as u32,
            BCRYPT_USE_SYSTEM_PREFERRED_RNG,
        )
    } < 0
    {
        return Err(Blocker::new(Gate::Manifest, "CNG_RUN_ID_FAILED"));
    }
    Ok(hex_bytes(&random))
}

#[derive(Debug)]
struct PinnedBinary {
    path: PathBuf,
    file: File,
    identity: NtfsFileIdentity,
    _ancestors: HeldDirectoryChain,
}

impl PinnedBinary {
    fn open() -> Result<Self, Blocker> {
        let contract = native_preflight_contract();
        let parent = contract
            .binary_path
            .parent()
            .ok_or_else(|| Blocker::new(Gate::BinaryIdentity, "BINARY_PARENT_MISSING"))?;
        let ancestors = HeldDirectoryChain::open(parent, Gate::BinaryIdentity)?;
        let file = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(&contract.binary_path)
            .map_err(|_| Blocker::new(Gate::BinaryIdentity, "PREFLIGHT_BINARY_OPEN_FAILED"))?;
        let attributes = file
            .metadata()
            .map_err(|_| Blocker::new(Gate::BinaryIdentity, "PREFLIGHT_BINARY_METADATA_FAILED"))?
            .file_attributes();
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(Blocker::new(
                Gate::BinaryIdentity,
                "PREFLIGHT_BINARY_REPARSE_FORBIDDEN",
            ));
        }
        let (volume, file_id, link_count, length) =
            file_id_from_handle(&file, Gate::BinaryIdentity)?;
        let (parent_volume, parent_file_id, _, _) =
            file_id_from_handle(ancestors.last(Gate::BinaryIdentity)?, Gate::BinaryIdentity)?;
        let sha256 = sha256_open_file(&file, Gate::BinaryIdentity)?;
        if volume != ancestors.volume_serial
            || parent_volume != volume
            || link_count != 1
            || length != contract.binary_length
            || sha256 != EXPECTED_CODEX_EXE_SHA256
        {
            return Err(Blocker::new(
                Gate::BinaryIdentity,
                "PREFLIGHT_BINARY_IDENTITY_MISMATCH",
            ));
        }
        verify_pe_file(&file)?;
        verify_authenticode(&contract.binary_path, &file)?;
        let identity = NtfsFileIdentity {
            path: contract.binary_path.clone(),
            volume_serial: volume,
            file_id,
            parent_file_id,
            length,
            sha256,
            link_count,
            creation_authority: FileCreationAuthority::OfficialReleaseCopy,
        };
        let pinned = Self {
            path: contract.binary_path,
            file,
            identity,
            _ancestors: ancestors,
        };
        pinned.revalidate()?;
        Ok(pinned)
    }

    fn revalidate(&self) -> Result<(), Blocker> {
        let (volume, file_id, link_count, length) =
            file_id_from_handle(&self.file, Gate::BinaryIdentity)?;
        let same_handle_hash = sha256_open_file(&self.file, Gate::BinaryIdentity)?;
        if volume != self.identity.volume_serial
            || file_id != self.identity.file_id
            || link_count != 1
            || length != self.identity.length
            || same_handle_hash != self.identity.sha256
        {
            return Err(Blocker::new(
                Gate::BinaryIdentity,
                "PREFLIGHT_PINNED_BINARY_CHANGED",
            ));
        }
        let reopened = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(&self.path)
            .map_err(|_| Blocker::new(Gate::BinaryIdentity, "PREFLIGHT_PATH_REOPEN_FAILED"))?;
        let (path_volume, path_file_id, path_links, path_length) =
            file_id_from_handle(&reopened, Gate::BinaryIdentity)?;
        if path_volume != volume
            || path_file_id != file_id
            || path_links != link_count
            || path_length != length
        {
            return Err(Blocker::new(
                Gate::BinaryIdentity,
                "PREFLIGHT_BINARY_PATH_CHANGED",
            ));
        }
        Ok(())
    }
}

#[derive(Debug)]
struct CreatedArtifact {
    identity: NtfsFileIdentity,
    _file: File,
}

fn create_new_artifact(
    path: &Path,
    parent: &File,
    bytes: &[u8],
    gate: Gate,
) -> Result<CreatedArtifact, Blocker> {
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|_| Blocker::new(gate, "ARTIFACT_CREATE_NEW_FAILED"))?;
    file.write_all(bytes)
        .map_err(|_| Blocker::new(gate, "ARTIFACT_WRITE_FAILED"))?;
    file.flush()
        .map_err(|_| Blocker::new(gate, "ARTIFACT_FLUSH_FAILED"))?;
    // SAFETY: file handle is live and opened with write access.
    if unsafe { FlushFileBuffers(file.as_raw_handle() as HANDLE) } == 0 {
        return Err(Blocker::new(gate, "ARTIFACT_OS_FLUSH_FAILED"));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| Blocker::new(gate, "ARTIFACT_SEEK_FAILED"))?;
    let attributes = file
        .metadata()
        .map_err(|_| Blocker::new(gate, "ARTIFACT_METADATA_FAILED"))?
        .file_attributes();
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(Blocker::new(gate, "ARTIFACT_REPARSE_FORBIDDEN"));
    }
    let (volume, file_id, link_count, length) = file_id_from_handle(&file, gate)?;
    let (parent_volume, parent_file_id, _, _) = file_id_from_handle(parent, gate)?;
    let sha256 = sha256_open_file(&file, gate)?;
    if volume != parent_volume || link_count != 1 || length != bytes.len() as u64 {
        return Err(Blocker::new(gate, "ARTIFACT_IDENTITY_INVALID"));
    }
    Ok(CreatedArtifact {
        identity: NtfsFileIdentity {
            path: path.to_path_buf(),
            volume_serial: volume,
            file_id,
            parent_file_id,
            length,
            sha256,
            link_count,
            creation_authority: FileCreationAuthority::NativeCreateNewOutput,
        },
        _file: file,
    })
}

#[derive(Debug)]
struct MaintenanceMutex {
    handle: Option<OwnedKernelHandle>,
    owned: bool,
}

impl MaintenanceMutex {
    fn acquire() -> Result<Self, Blocker> {
        let name = wide_null(OsStr::new(PREFLIGHT_MUTEX_NAME));
        // SAFETY: name is NUL-terminated and default security is requested.
        let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        if handle.is_null() {
            return Err(Blocker::new(
                Gate::JobPrepared,
                "PREFLIGHT_MAINTENANCE_MUTEX_CREATE_FAILED",
            ));
        }
        let handle = OwnedKernelHandle(handle);
        // SAFETY: handle is a live mutex handle and zero is a nonblocking wait.
        match unsafe { WaitForSingleObject(handle.0, 0) } {
            WAIT_OBJECT_0 => Ok(Self {
                handle: Some(handle),
                owned: true,
            }),
            WAIT_ABANDONED => {
                // SAFETY: WAIT_ABANDONED transfers ownership to this thread;
                // release it before returning the fail-closed recovery gate.
                let _ = unsafe { ReleaseMutex(handle.0) };
                Err(Blocker::new(
                    Gate::JobPrepared,
                    "PREFLIGHT_MAINTENANCE_MUTEX_ABANDONED",
                ))
            }
            WAIT_TIMEOUT => Err(Blocker::new(Gate::JobPrepared, "PREFLIGHT_ALREADY_RUNNING")),
            _ => Err(Blocker::new(
                Gate::JobPrepared,
                "PREFLIGHT_MAINTENANCE_MUTEX_WAIT_FAILED",
            )),
        }
    }

    fn release(mut self) -> Result<(), Blocker> {
        if self.owned {
            let handle = self
                .handle
                .as_ref()
                .ok_or_else(|| Blocker::new(Gate::CleanupFinalized, "MUTEX_HANDLE_MISSING"))?;
            // SAFETY: this process owns the mutex exactly once.
            if unsafe { ReleaseMutex(handle.0) } == 0 {
                return Err(Blocker::new(
                    Gate::CleanupFinalized,
                    "PREFLIGHT_MAINTENANCE_MUTEX_RELEASE_FAILED",
                ));
            }
            self.owned = false;
        }
        self.handle.take();
        Ok(())
    }
}

impl Drop for MaintenanceMutex {
    fn drop(&mut self) {
        if self.owned {
            if let Some(handle) = self.handle.as_ref() {
                // SAFETY: best-effort fail-closed release by the owning thread.
                let _ = unsafe { ReleaseMutex(handle.0) };
            }
            self.owned = false;
        }
    }
}

#[derive(Debug)]
struct PipeSet {
    stdin_child: Option<OwnedKernelHandle>,
    stdin_parent: Option<OwnedKernelHandle>,
    stdout_child: Option<OwnedKernelHandle>,
    stdout_parent: Option<OwnedKernelHandle>,
    stderr_child: Option<OwnedKernelHandle>,
    stderr_parent: Option<OwnedKernelHandle>,
}

impl PipeSet {
    fn create() -> Result<Self, Blocker> {
        let (stdin_child, stdin_parent) = create_inheritable_pipe(true)?;
        let (stdout_parent, stdout_child) = create_inheritable_pipe(false)?;
        let (stderr_parent, stderr_child) = create_inheritable_pipe(false)?;
        Ok(Self {
            stdin_child: Some(stdin_child),
            stdin_parent: Some(stdin_parent),
            stdout_child: Some(stdout_child),
            stdout_parent: Some(stdout_parent),
            stderr_child: Some(stderr_child),
            stderr_parent: Some(stderr_parent),
        })
    }

    fn inherited_handles(&self) -> Result<[HANDLE; 3], Blocker> {
        Ok([
            self.stdin_child
                .as_ref()
                .ok_or_else(|| Blocker::new(Gate::ChildCreatedSuspended, "STDIN_HANDLE_MISSING"))?
                .0,
            self.stdout_child
                .as_ref()
                .ok_or_else(|| Blocker::new(Gate::ChildCreatedSuspended, "STDOUT_HANDLE_MISSING"))?
                .0,
            self.stderr_child
                .as_ref()
                .ok_or_else(|| Blocker::new(Gate::ChildCreatedSuspended, "STDERR_HANDLE_MISSING"))?
                .0,
        ])
    }
}

fn create_inheritable_pipe(
    child_reads: bool,
) -> Result<(OwnedKernelHandle, OwnedKernelHandle), Blocker> {
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: std::ptr::null_mut(),
        bInheritHandle: 1,
    };
    let mut read = std::ptr::null_mut();
    let mut write = std::ptr::null_mut();
    // SAFETY: outputs and the initialized security attributes are writable.
    if unsafe { CreatePipe(&mut read, &mut write, &attributes, 0) } == 0
        || read.is_null()
        || write.is_null()
    {
        if !read.is_null() {
            // SAFETY: partial-success handle is owned by this function.
            let _ = unsafe { CloseHandle(read) };
        }
        if !write.is_null() {
            // SAFETY: partial-success handle is owned by this function.
            let _ = unsafe { CloseHandle(write) };
        }
        return Err(Blocker::new(
            Gate::ChildCreatedSuspended,
            "PREFLIGHT_PIPE_CREATE_FAILED",
        ));
    }
    let read = OwnedKernelHandle(read);
    let write = OwnedKernelHandle(write);
    let parent = if child_reads { &write } else { &read };
    // SAFETY: parent endpoint is live; clearing the inheritance bit prevents
    // the child from keeping its own output reader or stdin writer alive.
    if unsafe { SetHandleInformation(parent.0, HANDLE_FLAG_INHERIT, 0) } == 0 {
        return Err(Blocker::new(
            Gate::ChildCreatedSuspended,
            "PREFLIGHT_PIPE_INHERITANCE_CLEAR_FAILED",
        ));
    }
    Ok((read, write))
}

struct ProcThreadAttributeList {
    _storage: Vec<usize>,
    pointer: LPPROC_THREAD_ATTRIBUTE_LIST,
    job: Option<Box<HANDLE>>,
    inherited: Box<[HANDLE; 3]>,
    child_policy: Option<Box<u32>>,
}

impl ProcThreadAttributeList {
    fn create(job: HANDLE, inherited: &[HANDLE; 3], policy: LaunchPolicy) -> Result<Self, Blocker> {
        validate_live_handle(job, "PREFLIGHT_JOB_HANDLE_INVALID")?;
        for handle in inherited {
            validate_live_handle(*handle, "PREFLIGHT_PIPE_HANDLE_INVALID")?;
        }
        let attribute_count =
            1 + usize::from(policy.atomic_job_list) + usize::from(policy.child_process_restricted);
        let mut bytes = 0usize;
        // SAFETY: documented size-query call; bytes is writable.
        let _ = unsafe {
            InitializeProcThreadAttributeList(
                std::ptr::null_mut(),
                attribute_count as u32,
                0,
                &mut bytes,
            )
        };
        if bytes == 0 {
            return Err(Blocker::new(
                Gate::ChildCreatedSuspended,
                "PROC_THREAD_ATTRIBUTE_SIZE_FAILED",
            ));
        }
        let words = bytes.div_ceil(std::mem::size_of::<usize>());
        let mut storage = vec![0usize; words];
        let pointer = storage.as_mut_ptr().cast();
        // SAFETY: storage is aligned and at least the queried byte length.
        if unsafe {
            InitializeProcThreadAttributeList(pointer, attribute_count as u32, 0, &mut bytes)
        } == 0
        {
            return Err(Blocker::new(
                Gate::ChildCreatedSuspended,
                "PROC_THREAD_ATTRIBUTE_INIT_FAILED",
            ));
        }
        let list = Self {
            _storage: storage,
            pointer,
            job: policy.atomic_job_list.then(|| Box::new(job)),
            inherited: Box::new(*inherited),
            child_policy: policy
                .child_process_restricted
                .then(|| Box::new(PROCESS_CREATION_CHILD_PROCESS_RESTRICTED)),
        };
        if let Some(job) = list.job.as_deref() {
            let job_pointer = (job as *const HANDLE).cast();
            list.update(
                PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
                job_pointer,
                std::mem::size_of::<HANDLE>(),
                "PROC_THREAD_JOB_LIST_FAILED",
            )?;
        }
        if let Some(child_policy) = list.child_policy.as_deref() {
            let child_policy_pointer = (child_policy as *const u32).cast();
            list.update(
                PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY as usize,
                child_policy_pointer,
                std::mem::size_of::<u32>(),
                "PROC_THREAD_CHILD_POLICY_FAILED",
            )?;
        }
        let inherited_pointer = list.inherited.as_ptr().cast();
        list.update(
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
            inherited_pointer,
            std::mem::size_of::<[HANDLE; 3]>(),
            "PROC_THREAD_HANDLE_LIST_FAILED",
        )?;
        Ok(list)
    }

    fn update(
        &self,
        attribute: usize,
        value: *const core::ffi::c_void,
        size: usize,
        code: &'static str,
    ) -> Result<(), Blocker> {
        // SAFETY: pointer is initialized and value/size describe live storage
        // for the duration of this synchronous copy operation.
        if unsafe {
            UpdateProcThreadAttribute(
                self.pointer,
                0,
                attribute,
                value,
                size,
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(Blocker::new(Gate::ChildCreatedSuspended, code));
        }
        Ok(())
    }
}

impl Drop for ProcThreadAttributeList {
    fn drop(&mut self) {
        if !self.pointer.is_null() {
            // SAFETY: pointer was initialized once and is deleted once.
            unsafe { DeleteProcThreadAttributeList(self.pointer) };
        }
    }
}

struct LpacAttributeList {
    _storage: Vec<usize>,
    pointer: LPPROC_THREAD_ATTRIBUTE_LIST,
    job: Box<HANDLE>,
    child_policy: Box<u32>,
    security_capabilities: Box<SECURITY_CAPABILITIES>,
    all_application_packages_policy: Box<u32>,
}

impl LpacAttributeList {
    fn create(
        job: HANDLE,
        package_sid: windows_sys::Win32::Security::PSID,
    ) -> Result<Self, Blocker> {
        validate_live_handle(job, "LPAC_JOB_HANDLE_INVALID")?;
        if package_sid.is_null() {
            return Err(Blocker::new(
                Gate::AppContainerLpac,
                "LPAC_PACKAGE_SID_MISSING",
            ));
        }
        let mut bytes = 0usize;
        // SAFETY: documented four-entry size query; bytes is writable.
        let _ =
            unsafe { InitializeProcThreadAttributeList(std::ptr::null_mut(), 4, 0, &mut bytes) };
        if bytes == 0 {
            return Err(Blocker::new(
                Gate::ChildCreatedSuspended,
                "LPAC_ATTRIBUTE_SIZE_FAILED",
            ));
        }
        let mut storage = vec![0usize; bytes.div_ceil(std::mem::size_of::<usize>())];
        let pointer = storage.as_mut_ptr().cast();
        // SAFETY: storage is aligned and at least the queried byte length.
        if unsafe { InitializeProcThreadAttributeList(pointer, 4, 0, &mut bytes) } == 0 {
            return Err(Blocker::new(
                Gate::ChildCreatedSuspended,
                "LPAC_ATTRIBUTE_INIT_FAILED",
            ));
        }
        let list = Self {
            _storage: storage,
            pointer,
            job: Box::new(job),
            child_policy: Box::new(PROCESS_CREATION_CHILD_PROCESS_RESTRICTED),
            security_capabilities: Box::new(SECURITY_CAPABILITIES {
                AppContainerSid: package_sid,
                Capabilities: std::ptr::null_mut(),
                CapabilityCount: 0,
                Reserved: 0,
            }),
            all_application_packages_policy: Box::new(
                PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT,
            ),
        };
        list.update(
            PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
            (&*list.job as *const HANDLE).cast(),
            std::mem::size_of::<HANDLE>(),
            "LPAC_JOB_LIST_ATTRIBUTE_FAILED",
        )?;
        list.update(
            PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY as usize,
            (&*list.child_policy as *const u32).cast(),
            std::mem::size_of::<u32>(),
            "LPAC_CHILD_POLICY_ATTRIBUTE_FAILED",
        )?;
        list.update(
            PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES as usize,
            (&*list.security_capabilities as *const SECURITY_CAPABILITIES).cast(),
            std::mem::size_of::<SECURITY_CAPABILITIES>(),
            "LPAC_SECURITY_CAPABILITIES_ATTRIBUTE_FAILED",
        )?;
        list.update(
            PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY as usize,
            (&*list.all_application_packages_policy as *const u32).cast(),
            std::mem::size_of::<u32>(),
            "LPAC_ALL_APPLICATION_PACKAGES_ATTRIBUTE_FAILED",
        )?;
        Ok(list)
    }

    fn update(
        &self,
        attribute: usize,
        value: *const core::ffi::c_void,
        size: usize,
        code: &'static str,
    ) -> Result<(), Blocker> {
        // SAFETY: attribute list and backing values remain live through the
        // process creation call.
        if unsafe {
            UpdateProcThreadAttribute(
                self.pointer,
                0,
                attribute,
                value,
                size,
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(Blocker::new(Gate::ChildCreatedSuspended, code));
        }
        Ok(())
    }
}

impl Drop for LpacAttributeList {
    fn drop(&mut self) {
        if !self.pointer.is_null() {
            // SAFETY: pointer was initialized once and is deleted once.
            unsafe { DeleteProcThreadAttributeList(self.pointer) };
        }
    }
}

#[derive(Debug)]
struct LpacTokenObservation {
    is_appcontainer: bool,
    is_lpac: Option<bool>,
    direct_lpac_query_supported: bool,
    capability_count: u32,
    package_sid_match: bool,
}

#[derive(Debug)]
struct LpacChildCreateFailure {
    blocker: Blocker,
    child_created_suspended: bool,
    descendants_zero: bool,
    lpac_attribute_accepted: bool,
}

impl LpacChildCreateFailure {
    fn before_child(blocker: Blocker) -> Self {
        Self {
            blocker,
            child_created_suspended: false,
            descendants_zero: true,
            lpac_attribute_accepted: false,
        }
    }

    fn after_child(
        blocker: Blocker,
        descendants_zero: bool,
        lpac_attribute_accepted: bool,
    ) -> Self {
        Self {
            blocker,
            child_created_suspended: true,
            descendants_zero,
            lpac_attribute_accepted,
        }
    }

    fn profile_cleanup_allowed(&self) -> bool {
        !self.child_created_suspended || self.descendants_zero
    }
}

#[derive(Debug)]
struct SuspendedLpacChild {
    job: OwnedKernelHandle,
    process: OwnedKernelHandle,
    _thread: OwnedKernelHandle,
    drained: bool,
    child_process_policy_flags: u32,
}

impl SuspendedLpacChild {
    fn create(
        application_path: &Path,
        runtime: &RuntimeAuthority,
        environment_entries: &[(String, String)],
        package_sid: windows_sys::Win32::Security::PSID,
        create_win32_error: &mut Option<u32>,
    ) -> Result<Self, LpacChildCreateFailure> {
        let job = create_qualified_job().map_err(LpacChildCreateFailure::before_child)?;
        let attributes = LpacAttributeList::create(job.0, package_sid)
            .map_err(LpacChildCreateFailure::before_child)?;
        // SAFETY: zero is the baseline; the attribute list supplies every
        // security boundary and no standard handle inheritance is requested.
        let mut startup: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.lpAttributeList = attributes.pointer;
        let application = wide_null(application_path.as_os_str());
        let mut command_line =
            wide_null(OsStr::new(&format!("\"{}\"", application_path.display())));
        let current_directory = wide_null(runtime.run_root.as_os_str());
        let environment = build_environment_block(environment_entries)
            .map_err(LpacChildCreateFailure::before_child)?;
        // SAFETY: zero is the output baseline.
        let mut process_information: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
        // SAFETY: all buffers and the four-entry attribute list stay live for
        // this synchronous call. The primary thread is never resumed here.
        let created = unsafe {
            CreateProcessW(
                application.as_ptr(),
                command_line.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                0,
                CREATE_SUSPENDED
                    | DETACHED_PROCESS
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT,
                environment.as_ptr().cast(),
                current_directory.as_ptr(),
                &startup.StartupInfo as *const _,
                &mut process_information,
            )
        };
        let create_error = if created == 0 {
            unsafe { GetLastError() }
        } else {
            0
        };
        if created == 0 {
            *create_win32_error = Some(create_error);
        }
        if created == 0
            || process_information.hProcess.is_null()
            || process_information.hThread.is_null()
        {
            let child_observed = lpac_partial_child_observed(
                created != 0,
                !process_information.hProcess.is_null(),
                !process_information.hThread.is_null(),
            );
            let partial_process = (!process_information.hProcess.is_null())
                .then(|| OwnedKernelHandle(process_information.hProcess));
            let _partial_thread = (!process_information.hThread.is_null())
                .then(|| OwnedKernelHandle(process_information.hThread));
            if let Some(process) = partial_process.as_ref() {
                let direct_terminated =
                    unsafe { TerminateProcess(process.0, PREFLIGHT_TERMINATE_EXIT) } != 0;
                let job_terminated =
                    unsafe { TerminateJobObject(job.0, PREFLIGHT_TERMINATE_EXIT) } != 0;
                let wait = unsafe { WaitForSingleObject(process.0, 5_000) };
                if !lpac_partial_cleanup_proven(direct_terminated, job_terminated, wait) {
                    return Err(LpacChildCreateFailure::after_child(
                        Blocker::new(Gate::JobDrained, "LPAC_PARTIAL_CREATE_DRAIN_UNPROVEN"),
                        false,
                        created != 0,
                    ));
                }
            } else if child_observed {
                return Err(LpacChildCreateFailure::after_child(
                    Blocker::new(
                        Gate::JobDrained,
                        "LPAC_PARTIAL_CREATE_PROCESS_HANDLE_MISSING",
                    ),
                    false,
                    created != 0,
                ));
            }
            let blocker = create_process_blocker(create_error);
            return Err(if child_observed {
                LpacChildCreateFailure::after_child(blocker, true, created != 0)
            } else {
                LpacChildCreateFailure::before_child(blocker)
            });
        }
        let mut child = Self {
            job,
            process: OwnedKernelHandle(process_information.hProcess),
            _thread: OwnedKernelHandle(process_information.hThread),
            drained: false,
            child_process_policy_flags: 0,
        };
        let flags = match query_child_process_policy_flags(child.process.0) {
            Ok(value) => value,
            Err(error) => {
                return match child.terminate_and_drain() {
                    Ok(()) => Err(LpacChildCreateFailure::after_child(error, true, true)),
                    Err(cleanup_error) => Err(LpacChildCreateFailure::after_child(
                        cleanup_error,
                        false,
                        true,
                    )),
                };
            }
        };
        child.child_process_policy_flags = flags;
        let accounting = match query_job_accounting(&child.job, Gate::JobPrepared) {
            Ok(value) => value,
            Err(error) => {
                return match child.terminate_and_drain() {
                    Ok(()) => Err(LpacChildCreateFailure::after_child(error, true, true)),
                    Err(cleanup_error) => Err(LpacChildCreateFailure::after_child(
                        cleanup_error,
                        false,
                        true,
                    )),
                };
            }
        };
        if flags != 1 || accounting.TotalProcesses != 1 || accounting.ActiveProcesses != 1 {
            let blocker = Blocker::new(Gate::PrelaunchObserved, "LPAC_PRELAUNCH_READBACK_MISMATCH");
            return match child.terminate_and_drain() {
                Ok(()) => Err(LpacChildCreateFailure::after_child(blocker, true, true)),
                Err(cleanup_error) => Err(LpacChildCreateFailure::after_child(
                    cleanup_error,
                    false,
                    true,
                )),
            };
        }
        Ok(child)
    }

    fn observe_token(
        &self,
        expected_package_sid: windows_sys::Win32::Security::PSID,
    ) -> Result<LpacTokenObservation, Blocker> {
        let mut token = std::ptr::null_mut();
        // SAFETY: process handle is live and token output is writable.
        if unsafe { OpenProcessToken(self.process.0, TOKEN_QUERY | TOKEN_QUERY_SOURCE, &mut token) }
            == 0
            || token.is_null()
        {
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "LPAC_PROCESS_TOKEN_OPEN_FAILED",
            ));
        }
        let token = OwnedKernelHandle(token);
        let is_appcontainer =
            query_token_u32(token.0, TokenIsAppContainer, "LPAC_TOKEN_APP_QUERY_FAILED")?;
        let direct_lpac = match query_token_u32(
            token.0,
            TokenIsLessPrivilegedAppContainer,
            "LPAC_TOKEN_LPAC_QUERY_FAILED",
        ) {
            Ok(value) => Some(value == 1),
            Err(error) if error.code == "LPAC_DIRECT_TOKEN_READBACK_UNSUPPORTED" => None,
            Err(error) => return Err(error),
        };
        let capabilities = query_token_buffer(
            token.0,
            TokenCapabilities,
            std::mem::size_of::<u32>(),
            65_536,
            "LPAC_TOKEN_CAPABILITIES_QUERY_FAILED",
        )?;
        // TOKEN_GROUPS begins with the exact DWORD count; zero capabilities
        // means no following SID_AND_ATTRIBUTES entry may be consumed.
        let capability_count = unsafe { *(capabilities.as_ptr().cast::<u32>()) };
        let appcontainer = query_token_buffer(
            token.0,
            TokenAppContainerSid,
            std::mem::size_of::<TOKEN_APPCONTAINER_INFORMATION>(),
            4_096,
            "LPAC_TOKEN_PACKAGE_SID_QUERY_FAILED",
        )?;
        if appcontainer.len() * std::mem::size_of::<usize>()
            < std::mem::size_of::<TOKEN_APPCONTAINER_INFORMATION>()
        {
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "LPAC_TOKEN_PACKAGE_SID_BUFFER_INVALID",
            ));
        }
        let token_package_sid = unsafe {
            (*(appcontainer
                .as_ptr()
                .cast::<TOKEN_APPCONTAINER_INFORMATION>()))
            .TokenAppContainer
        };
        let package_sid_match = !token_package_sid.is_null()
            && unsafe { IsValidSid(token_package_sid) } != 0
            && !expected_package_sid.is_null()
            && unsafe {
                windows_sys::Win32::Security::EqualSid(token_package_sid, expected_package_sid) != 0
            };
        Ok(LpacTokenObservation {
            is_appcontainer: is_appcontainer == 1,
            is_lpac: direct_lpac,
            direct_lpac_query_supported: direct_lpac.is_some(),
            capability_count,
            package_sid_match,
        })
    }

    fn terminate_and_drain(&mut self) -> Result<(), Blocker> {
        // SAFETY: the job is live and owns the still-suspended process.
        if unsafe { TerminateJobObject(self.job.0, PREFLIGHT_TERMINATE_EXIT) } == 0 {
            return Err(Blocker::new(
                Gate::JobTerminated,
                "LPAC_JOB_TERMINATE_FAILED",
            ));
        }
        // SAFETY: process handle is live and bounded wait is fail-closed.
        if unsafe { WaitForSingleObject(self.process.0, 5_000) } != WAIT_OBJECT_0 {
            return Err(Blocker::new(Gate::JobDrained, "LPAC_PROCESS_WAIT_FAILED"));
        }
        let accounting = query_job_accounting(&self.job, Gate::JobDrained)?;
        if accounting.TotalProcesses != 1 || accounting.ActiveProcesses != 0 {
            return Err(Blocker::new(
                Gate::JobDrained,
                "LPAC_JOB_DRAIN_READBACK_MISMATCH",
            ));
        }
        self.drained = true;
        Ok(())
    }
}

impl Drop for SuspendedLpacChild {
    fn drop(&mut self) {
        if !self.drained {
            let _ = unsafe { TerminateJobObject(self.job.0, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { WaitForSingleObject(self.process.0, 5_000) };
        }
    }
}

fn lpac_partial_cleanup_proven(
    direct_terminated: bool,
    job_terminated: bool,
    wait_result: u32,
) -> bool {
    (direct_terminated || job_terminated) && wait_result == WAIT_OBJECT_0
}

fn lpac_partial_child_observed(
    create_reported_success: bool,
    process_handle_present: bool,
    thread_handle_present: bool,
) -> bool {
    create_reported_success || process_handle_present || thread_handle_present
}

fn query_token_u32(
    token: HANDLE,
    class: windows_sys::Win32::Security::TOKEN_INFORMATION_CLASS,
    code: &'static str,
) -> Result<u32, Blocker> {
    let mut value = 0u32;
    let mut returned = 0u32;
    // SAFETY: exact DWORD output buffer is writable for the requested class.
    let queried = unsafe {
        GetTokenInformation(
            token,
            class,
            (&mut value as *mut u32).cast(),
            std::mem::size_of::<u32>() as u32,
            &mut returned,
        )
    };
    if queried == 0 {
        let error = unsafe { GetLastError() };
        if class == TokenIsLessPrivilegedAppContainer && error == ERROR_INVALID_PARAMETER {
            returned = 0;
            // SAFETY: documented fixed-DWORD token query. This bypasses an
            // older advapi32 information-class gate while using the same live
            // handle and exact class directly against ntdll.
            let status = unsafe {
                NtQueryInformationToken(
                    token,
                    class,
                    (&mut value as *mut u32).cast(),
                    std::mem::size_of::<u32>() as u32,
                    &mut returned,
                )
            };
            classify_lpac_nt_query_status(status)?;
        } else {
            return Err(Blocker::new(Gate::PrelaunchObserved, code));
        }
    }
    if returned != std::mem::size_of::<u32>() as u32 || value > 1 {
        return Err(Blocker::new(Gate::PrelaunchObserved, code));
    }
    Ok(value)
}

fn classify_lpac_nt_query_status(status: i32) -> Result<(), Blocker> {
    if status >= 0 {
        return Ok(());
    }
    let code = match status as u32 {
        0xC000_0002 | 0xC000_0003 | 0xC000_00BB => "LPAC_DIRECT_TOKEN_READBACK_UNSUPPORTED",
        _ => "LPAC_TOKEN_NT_QUERY_FAILED",
    };
    Err(Blocker::new(Gate::PrelaunchObserved, code))
}

fn query_token_buffer(
    token: HANDLE,
    class: windows_sys::Win32::Security::TOKEN_INFORMATION_CLASS,
    minimum_bytes: usize,
    maximum_bytes: usize,
    code: &'static str,
) -> Result<Vec<usize>, Blocker> {
    let mut bytes = 0u32;
    // SAFETY: documented size query with null output.
    let first = unsafe { GetTokenInformation(token, class, std::ptr::null_mut(), 0, &mut bytes) };
    if first != 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        return Err(Blocker::new(Gate::PrelaunchObserved, code));
    }
    validate_token_buffer_length(bytes as usize, minimum_bytes, maximum_bytes)?;
    let words = (bytes as usize).div_ceil(std::mem::size_of::<usize>());
    let mut storage = vec![0usize; words];
    let mut returned = 0u32;
    // SAFETY: aligned storage has at least the size returned by the first call.
    if unsafe {
        GetTokenInformation(
            token,
            class,
            storage.as_mut_ptr().cast(),
            bytes,
            &mut returned,
        )
    } == 0
        || returned != bytes
    {
        return Err(Blocker::new(Gate::PrelaunchObserved, code));
    }
    Ok(storage)
}

fn validate_token_buffer_length(
    bytes: usize,
    minimum_bytes: usize,
    maximum_bytes: usize,
) -> Result<(), Blocker> {
    if minimum_bytes == 0
        || maximum_bytes < minimum_bytes
        || bytes < minimum_bytes
        || bytes > maximum_bytes
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "LPAC_TOKEN_BUFFER_LENGTH_INVALID",
        ));
    }
    Ok(())
}

#[derive(Debug)]
struct BoundedCapture {
    bytes: Vec<u8>,
    total_bytes: usize,
    overflow: bool,
}

fn read_bounded(mut file: impl Read, limit: usize) -> Result<BoundedCapture, Blocker> {
    let mut bytes = Vec::with_capacity(limit.min(64 * 1024));
    let mut total_bytes = 0usize;
    let mut overflow = false;
    let mut buffer = [0u8; 16 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| Blocker::new(Gate::OutputAndDeadline, "PREFLIGHT_PIPE_READ_FAILED"))?;
        if count == 0 {
            break;
        }
        total_bytes = total_bytes.checked_add(count).ok_or_else(|| {
            Blocker::new(Gate::OutputAndDeadline, "PREFLIGHT_OUTPUT_SIZE_OVERFLOW")
        })?;
        let remaining = limit.saturating_sub(bytes.len());
        let retained = remaining.min(count);
        bytes.extend_from_slice(&buffer[..retained]);
        overflow |= retained != count || total_bytes > limit;
    }
    Ok(BoundedCapture {
        bytes,
        total_bytes,
        overflow,
    })
}

#[derive(Debug)]
struct ChildTransaction {
    job: OwnedKernelHandle,
    process: OwnedKernelHandle,
    thread: OwnedKernelHandle,
    pipes: PipeSet,
    stdout_reader: Option<JoinHandle<Result<BoundedCapture, Blocker>>>,
    stderr_reader: Option<JoinHandle<Result<BoundedCapture, Blocker>>>,
    resumed: bool,
    drained: bool,
    pre_resume_total_processes: u32,
    pre_resume_active_processes: u32,
    max_observed_total_processes: u32,
    max_observed_active_processes: u32,
    child_process_policy_flags: u32,
    deadline: Duration,
}

#[derive(Debug)]
struct ChildOutcome {
    exit_code: u32,
    timed_out: bool,
    stdout: BoundedCapture,
    stderr: BoundedCapture,
    total_processes: u32,
    active_processes: u32,
    max_observed_total_processes: u32,
    max_observed_active_processes: u32,
}

impl ChildTransaction {
    fn create(
        application_path: &Path,
        command_line: &OsStr,
        runtime: &RuntimeAuthority,
        environment_entries: &[(String, String)],
        policy: LaunchPolicy,
        console_creation_mode: ConsoleCreationMode,
        deadline: Duration,
    ) -> Result<Self, Blocker> {
        let job = create_qualified_job()?;
        let pipes = PipeSet::create()?;
        let inherited = pipes.inherited_handles()?;
        let attributes = ProcThreadAttributeList::create(job.0, &inherited, policy)?;
        // SAFETY: zero is the baseline; every required field is assigned.
        let mut startup: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = inherited[0];
        startup.StartupInfo.hStdOutput = inherited[1];
        startup.StartupInfo.hStdError = inherited[2];
        startup.lpAttributeList = attributes.pointer;

        let mut command_line = wide_null(command_line);
        let application = wide_null(application_path.as_os_str());
        let current_directory = wide_null(runtime.run_root.as_os_str());
        let environment = build_environment_block(environment_entries)?;
        // SAFETY: zero is the output baseline.
        let mut process_information: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
        // SAFETY: all string/buffer pointers remain live through the call;
        // STARTUPINFOEX contains a valid three-entry attribute list and only
        // the three pipe endpoints are inheritable.
        let created = unsafe {
            CreateProcessW(
                application.as_ptr(),
                command_line.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
                CREATE_SUSPENDED
                    | console_creation_mode.flag()
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT,
                environment.as_ptr().cast(),
                current_directory.as_ptr(),
                &startup.StartupInfo as *const _,
                &mut process_information,
            )
        };
        // SAFETY: sampled immediately after CreateProcessW, before cleanup can
        // overwrite the thread-local Win32 diagnostic.
        let create_error = if created == 0 {
            unsafe { GetLastError() }
        } else {
            0
        };
        if created == 0
            || process_information.hProcess.is_null()
            || process_information.hThread.is_null()
        {
            if !process_information.hProcess.is_null() {
                // SAFETY: partial-success process is terminated and closed.
                let _ = unsafe {
                    TerminateProcess(process_information.hProcess, PREFLIGHT_TERMINATE_EXIT)
                };
                let _ = unsafe { WaitForSingleObject(process_information.hProcess, 5_000) };
                let _ = unsafe { CloseHandle(process_information.hProcess) };
            }
            if !process_information.hThread.is_null() {
                // SAFETY: partial-success thread handle is closed once.
                let _ = unsafe { CloseHandle(process_information.hThread) };
            }
            return Err(create_process_blocker(create_error));
        }
        let child_process_policy_flags =
            match query_child_process_policy_flags(process_information.hProcess) {
                Ok(flags) => flags,
                Err(error) => {
                    let _ = unsafe {
                        TerminateProcess(process_information.hProcess, PREFLIGHT_TERMINATE_EXIT)
                    };
                    let _ = unsafe { TerminateJobObject(job.0, PREFLIGHT_TERMINATE_EXIT) };
                    let _ = unsafe { WaitForSingleObject(process_information.hProcess, 5_000) };
                    let _ = unsafe { CloseHandle(process_information.hProcess) };
                    let _ = unsafe { CloseHandle(process_information.hThread) };
                    return Err(error);
                }
            };
        if child_process_policy_flags != u32::from(policy.child_process_restricted) {
            let _ =
                unsafe { TerminateProcess(process_information.hProcess, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { TerminateJobObject(job.0, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { WaitForSingleObject(process_information.hProcess, 5_000) };
            let _ = unsafe { CloseHandle(process_information.hProcess) };
            let _ = unsafe { CloseHandle(process_information.hThread) };
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "CHILD_PROCESS_POLICY_READBACK_MISMATCH",
            ));
        }
        Ok(Self {
            job,
            process: OwnedKernelHandle(process_information.hProcess),
            thread: OwnedKernelHandle(process_information.hThread),
            pipes,
            stdout_reader: None,
            stderr_reader: None,
            resumed: false,
            drained: false,
            pre_resume_total_processes: 0,
            pre_resume_active_processes: 0,
            max_observed_total_processes: 0,
            max_observed_active_processes: 0,
            child_process_policy_flags,
            deadline,
        })
    }

    fn prepare_job_membership(&mut self, policy: LaunchPolicy) -> Result<(), Blocker> {
        if policy.assigned_after_create {
            // SAFETY: the process primary thread is still suspended, so this
            // assignment completes before any child code can run or spawn.
            if unsafe { AssignProcessToJobObject(self.job.0, self.process.0) } == 0 {
                let blocker =
                    Blocker::new(Gate::JobPrepared, "DIAGNOSTIC_ASSIGN_PROCESS_TO_JOB_FAILED");
                return match self.terminate_and_drain() {
                    Ok(_) => Err(blocker),
                    Err(cleanup_error) => Err(cleanup_error),
                };
            }
        }
        let accounting = match query_job_accounting(&self.job, Gate::JobPrepared) {
            Ok(value) => value,
            Err(error) => {
                return match self.terminate_and_drain() {
                    Ok(_) => Err(error),
                    Err(cleanup_error) => Err(cleanup_error),
                };
            }
        };
        self.pre_resume_total_processes = accounting.TotalProcesses;
        self.pre_resume_active_processes = accounting.ActiveProcesses;
        self.observe_accounting(&accounting);
        if accounting.TotalProcesses != 1 || accounting.ActiveProcesses != 1 {
            let blocker = Blocker::new(
                Gate::JobPrepared,
                "PREFLIGHT_PRE_RESUME_JOB_MEMBERSHIP_INVALID",
            );
            return match self.terminate_and_drain() {
                Ok(_) => Err(blocker),
                Err(cleanup_error) => Err(cleanup_error),
            };
        }
        Ok(())
    }

    fn observe_accounting(&mut self, accounting: &JOBOBJECT_BASIC_ACCOUNTING_INFORMATION) {
        self.max_observed_total_processes = self
            .max_observed_total_processes
            .max(accounting.TotalProcesses);
        self.max_observed_active_processes = self
            .max_observed_active_processes
            .max(accounting.ActiveProcesses);
    }

    fn sample_job_accounting(
        &mut self,
        gate: Gate,
    ) -> Result<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, Blocker> {
        let accounting = query_job_accounting(&self.job, gate)?;
        self.observe_accounting(&accounting);
        Ok(accounting)
    }

    fn start_readers_and_close_parent_input(&mut self) -> Result<(), Blocker> {
        self.pipes.stdin_parent.take();
        self.pipes.stdin_child.take();
        self.pipes.stdout_child.take();
        self.pipes.stderr_child.take();
        let stdout = self
            .pipes
            .stdout_parent
            .take()
            .ok_or_else(|| Blocker::new(Gate::OutputAndDeadline, "STDOUT_PARENT_MISSING"))?;
        let stderr = self
            .pipes
            .stderr_parent
            .take()
            .ok_or_else(|| Blocker::new(Gate::OutputAndDeadline, "STDERR_PARENT_MISSING"))?;
        // SAFETY: each owned raw handle is transferred exactly once to File.
        let stdout_file = unsafe { File::from_raw_handle(stdout.into_raw().cast()) };
        // SAFETY: same ownership transfer as stdout.
        let stderr_file = unsafe { File::from_raw_handle(stderr.into_raw().cast()) };
        self.stdout_reader = Some(
            thread::Builder::new()
                .name("pkm-preflight-stdout".to_owned())
                .spawn(move || read_bounded(stdout_file, MAX_STDOUT_BYTES))
                .map_err(|_| Blocker::new(Gate::OutputAndDeadline, "STDOUT_READER_START_FAILED"))?,
        );
        self.stderr_reader = Some(
            thread::Builder::new()
                .name("pkm-preflight-stderr".to_owned())
                .spawn(move || read_bounded(stderr_file, MAX_STDERR_BYTES))
                .map_err(|_| Blocker::new(Gate::OutputAndDeadline, "STDERR_READER_START_FAILED"))?,
        );
        Ok(())
    }

    fn run(&mut self) -> Result<ChildOutcome, Blocker> {
        self.start_readers_and_close_parent_input()?;
        // SAFETY: thread handle is the live primary thread created suspended.
        if unsafe { ResumeThread(self.thread.0) } == u32::MAX {
            self.resumed = true;
            let cleanup = self.terminate_and_drain();
            return match cleanup {
                Ok(_) => Err(Blocker::new(
                    Gate::ChildResumed,
                    "PREFLIGHT_RESUME_FAILED_POSSIBLY_RAN",
                )),
                Err(error) => Err(error),
            };
        }
        self.resumed = true;
        let started = Instant::now();
        let mut timed_out = false;
        self.sample_job_accounting(Gate::ChildResumed)?;
        loop {
            let elapsed = started.elapsed();
            if elapsed >= self.deadline {
                timed_out = true;
                break;
            }
            let remaining = self.deadline.saturating_sub(elapsed);
            let wait_ms = remaining.as_millis().clamp(1, 25) as u32;
            // SAFETY: process handle is live for this wait.
            match unsafe { WaitForSingleObject(self.process.0, wait_ms.max(1)) } {
                WAIT_OBJECT_0 => break,
                WAIT_TIMEOUT => {
                    self.sample_job_accounting(Gate::ChildResumed)?;
                    continue;
                }
                _ => {
                    let cleanup = self.terminate_and_drain();
                    return match cleanup {
                        Ok(_) => Err(Blocker::new(
                            Gate::OutputAndDeadline,
                            "PREFLIGHT_PROCESS_WAIT_FAILED",
                        )),
                        Err(error) => Err(error),
                    };
                }
            }
        }
        if timed_out {
            self.terminate_and_drain()?;
        } else if let Err(error) = self.wait_for_zero_descendants(false) {
            return match self.terminate_and_drain() {
                Ok(_) => Err(error),
                Err(cleanup_error) => Err(cleanup_error),
            };
        }
        let mut exit_code = 0u32;
        // SAFETY: process has signaled and output points to writable storage.
        if unsafe { GetExitCodeProcess(self.process.0, &mut exit_code) } == 0 {
            return Err(Blocker::new(
                Gate::OutputAndDeadline,
                "PREFLIGHT_EXIT_CODE_QUERY_FAILED",
            ));
        }
        let accounting = self.sample_job_accounting(Gate::JobDrained)?;
        let stdout = join_capture(&mut self.stdout_reader, "STDOUT_READER_JOIN_FAILED")?;
        let stderr = join_capture(&mut self.stderr_reader, "STDERR_READER_JOIN_FAILED")?;
        Ok(ChildOutcome {
            exit_code,
            timed_out,
            stdout,
            stderr,
            total_processes: accounting.TotalProcesses,
            active_processes: accounting.ActiveProcesses,
            max_observed_total_processes: self.max_observed_total_processes,
            max_observed_active_processes: self.max_observed_active_processes,
        })
    }

    fn terminate_and_drain(&mut self) -> Result<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, Blocker> {
        // SAFETY: both handles are live. Direct termination prevents any
        // post-create, pre-job-proof failure from escaping solely because of a
        // later Job API error; job termination covers descendants.
        let _ = unsafe { TerminateProcess(self.process.0, PREFLIGHT_TERMINATE_EXIT) };
        if unsafe { TerminateJobObject(self.job.0, PREFLIGHT_TERMINATE_EXIT) } == 0 {
            return Err(Blocker::new(
                Gate::JobTerminated,
                "PREFLIGHT_TERMINATE_JOB_FAILED",
            ));
        }
        self.wait_for_zero_descendants(true)
    }

    fn wait_for_zero_descendants(
        &mut self,
        cleanup: bool,
    ) -> Result<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, Blocker> {
        let deadline = Instant::now() + PREFLIGHT_CLEANUP_WAIT;
        loop {
            // SAFETY: process handle remains live while transaction owns it.
            let wait = unsafe { WaitForSingleObject(self.process.0, 100) };
            if wait != WAIT_OBJECT_0 && wait != WAIT_TIMEOUT {
                return Err(Blocker::new(
                    Gate::JobDrained,
                    "PREFLIGHT_PROCESS_DRAIN_WAIT_FAILED",
                ));
            }
            let accounting = self.sample_job_accounting(Gate::JobDrained)?;
            if wait == WAIT_OBJECT_0 && accounting.ActiveProcesses == 0 {
                self.drained = true;
                return Ok(accounting);
            }
            if Instant::now() >= deadline {
                return Err(Blocker::new(
                    Gate::JobDrained,
                    if cleanup {
                        "PREFLIGHT_CLEANUP_DRAIN_TIMEOUT"
                    } else {
                        "PREFLIGHT_JOB_DRAIN_TIMEOUT"
                    },
                ));
            }
        }
    }
}

fn create_process_blocker(error: u32) -> Blocker {
    let code = match error {
        ERROR_ACCESS_DENIED => "PREFLIGHT_CREATE_PROCESS_ACCESS_DENIED",
        ERROR_BAD_EXE_FORMAT => "PREFLIGHT_CREATE_PROCESS_BAD_EXE_FORMAT",
        ERROR_ELEVATION_REQUIRED => "PREFLIGHT_CREATE_PROCESS_ELEVATION_REQUIRED",
        ERROR_INVALID_HANDLE => "PREFLIGHT_CREATE_PROCESS_INVALID_HANDLE",
        ERROR_INVALID_PARAMETER => "PREFLIGHT_CREATE_PROCESS_INVALID_PARAMETER",
        ERROR_NOT_SUPPORTED => "PREFLIGHT_CREATE_PROCESS_NOT_SUPPORTED",
        _ => "PREFLIGHT_CREATE_PROCESS_FAILED",
    };
    Blocker::new(Gate::ChildCreatedSuspended, code)
}

fn validate_live_handle(handle: HANDLE, code: &'static str) -> Result<(), Blocker> {
    let mut flags = 0u32;
    // SAFETY: handle is expected live and flags is writable.
    if handle.is_null() || unsafe { GetHandleInformation(handle, &mut flags) } == 0 {
        return Err(Blocker::new(Gate::ChildCreatedSuspended, code));
    }
    Ok(())
}

impl Drop for ChildTransaction {
    fn drop(&mut self) {
        if !self.drained {
            // SAFETY: best-effort fail-closed fallback; explicit paths above
            // always attempt direct termination, job termination, and drain.
            let _ = unsafe { TerminateProcess(self.process.0, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { TerminateJobObject(self.job.0, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { WaitForSingleObject(self.process.0, 5_000) };
            // A failed termination/drain proof must not turn Drop into an
            // unbounded wait on a pipe reader. Dropping JoinHandle detaches the
            // bounded reader; KILL_ON_JOB_CLOSE remains the final fail-closed
            // process-tree boundary when the job field is dropped below.
            self.stdout_reader.take();
            self.stderr_reader.take();
            return;
        }
        let _ = join_capture(&mut self.stdout_reader, "STDOUT_READER_JOIN_FAILED");
        let _ = join_capture(&mut self.stderr_reader, "STDERR_READER_JOIN_FAILED");
    }
}

fn join_capture(
    reader: &mut Option<JoinHandle<Result<BoundedCapture, Blocker>>>,
    code: &'static str,
) -> Result<BoundedCapture, Blocker> {
    reader
        .take()
        .ok_or_else(|| Blocker::new(Gate::OutputAndDeadline, code))?
        .join()
        .map_err(|_| Blocker::new(Gate::OutputAndDeadline, code))?
}

fn query_job_accounting(
    job: &OwnedKernelHandle,
    gate: Gate,
) -> Result<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, Blocker> {
    // SAFETY: zero is the output baseline.
    let mut accounting: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { std::mem::zeroed() };
    let mut returned = 0u32;
    // SAFETY: job and output buffer are live with exact size.
    if unsafe {
        QueryInformationJobObject(
            job.0,
            JobObjectBasicAccountingInformation,
            (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
            std::mem::size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
            &mut returned,
        )
    } == 0
        || returned != std::mem::size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32
    {
        return Err(Blocker::new(gate, "PREFLIGHT_JOB_ACCOUNTING_QUERY_FAILED"));
    }
    Ok(accounting)
}

fn query_child_process_policy_flags(process: HANDLE) -> Result<u32, Blocker> {
    // SAFETY: zero is the documented output baseline.
    let mut policy: PROCESS_MITIGATION_CHILD_PROCESS_POLICY = unsafe { std::mem::zeroed() };
    // SAFETY: process is a live process handle and the output buffer has the
    // exact policy type and size required by ProcessChildProcessPolicy.
    if unsafe {
        GetProcessMitigationPolicy(
            process,
            ProcessChildProcessPolicy,
            (&mut policy as *mut PROCESS_MITIGATION_CHILD_PROCESS_POLICY).cast(),
            std::mem::size_of::<PROCESS_MITIGATION_CHILD_PROCESS_POLICY>(),
        )
    } == 0
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "CHILD_PROCESS_POLICY_READBACK_FAILED",
        ));
    }
    // SAFETY: GetProcessMitigationPolicy initialized the active union member.
    Ok(unsafe { policy.Anonymous.Flags })
}

fn appcontainer_local_appdata(
    package_sid: windows_sys::Win32::Security::PSID,
) -> Result<String, Blocker> {
    if package_sid.is_null() {
        return Err(Blocker::new(
            Gate::AppContainerLpac,
            "LPAC_PACKAGE_SID_MISSING",
        ));
    }
    let mut sid_text = std::ptr::null_mut();
    // SAFETY: package SID is live and the output pointer is writable.
    if unsafe { ConvertSidToStringSidW(package_sid, &mut sid_text) } == 0 || sid_text.is_null() {
        return Err(Blocker::new(
            Gate::AppContainerLpac,
            "LPAC_PACKAGE_SID_STRING_FAILED",
        ));
    }
    let sid_string = wide_pointer_to_string(sid_text, 512, "LPAC_PACKAGE_SID_STRING_INVALID");
    // SAFETY: ConvertSidToStringSidW returns LocalFree-owned storage.
    let _ = unsafe { LocalFree(sid_text.cast()) };
    let sid_string = sid_string?;
    let sid_wide = wide_null(OsStr::new(&sid_string));
    let mut folder = std::ptr::null_mut();
    // SAFETY: SID text is NUL-terminated and folder output is writable.
    if unsafe { GetAppContainerFolderPath(sid_wide.as_ptr(), &mut folder) } < 0 || folder.is_null()
    {
        return Err(Blocker::new(
            Gate::AppContainerLpac,
            "LPAC_LOCALAPPDATA_QUERY_FAILED",
        ));
    }
    let folder_string = wide_pointer_to_string(folder, 32_768, "LPAC_LOCALAPPDATA_INVALID");
    // SAFETY: GetAppContainerFolderPath returns CoTaskMemFree-owned storage.
    unsafe { CoTaskMemFree(folder.cast()) };
    folder_string
}

fn wide_pointer_to_string(
    pointer: *const u16,
    limit: usize,
    code: &'static str,
) -> Result<String, Blocker> {
    if pointer.is_null() || limit == 0 {
        return Err(Blocker::new(Gate::AppContainerLpac, code));
    }
    let mut length = 0usize;
    // SAFETY: callers provide a system-allocated NUL-terminated UTF-16
    // string. Reads are bounded so malformed output fails closed.
    while length < limit && unsafe { *pointer.add(length) } != 0 {
        length += 1;
    }
    if length == 0 || length == limit {
        return Err(Blocker::new(Gate::AppContainerLpac, code));
    }
    let units = unsafe { std::slice::from_raw_parts(pointer, length) };
    String::from_utf16(units).map_err(|_| Blocker::new(Gate::AppContainerLpac, code))
}

fn build_environment_entries(runtime: &RuntimeAuthority) -> Result<Vec<(String, String)>, Blocker> {
    let codex_home = path_utf8(&runtime.codex_home, Gate::Manifest)?;
    let temp = path_utf8(&runtime.temp, Gate::Manifest)?;
    let mut entries = vec![
        ("ALL_PROXY".to_owned(), String::new()),
        ("CODEX_HOME".to_owned(), codex_home),
        ("HTTP_PROXY".to_owned(), String::new()),
        ("HTTPS_PROXY".to_owned(), String::new()),
        ("NO_PROXY".to_owned(), "*".to_owned()),
        ("SYSTEMROOT".to_owned(), r"C:\Windows".to_owned()),
        ("TEMP".to_owned(), temp.clone()),
        ("TMP".to_owned(), temp),
    ];
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    if entries
        .iter()
        .any(|(key, value)| key.contains(['=', '\0']) || value.contains('\0'))
    {
        return Err(Blocker::new(
            Gate::Manifest,
            "PREFLIGHT_ENVIRONMENT_INVALID",
        ));
    }
    Ok(entries)
}

fn build_lpac_environment_entries(
    runtime: &RuntimeAuthority,
    package_sid: windows_sys::Win32::Security::PSID,
) -> Result<Vec<(String, String)>, Blocker> {
    let mut entries = build_environment_entries(runtime)?;
    entries.push((
        "LOCALAPPDATA".to_owned(),
        appcontainer_local_appdata(package_sid)?,
    ));
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(entries)
}

fn build_environment_block(entries: &[(String, String)]) -> Result<Vec<u16>, Blocker> {
    let mut block = Vec::new();
    for (key, value) in entries {
        block.extend(OsStr::new(&format!("{key}={value}")).encode_wide());
        block.push(0);
    }
    block.push(0);
    Ok(block)
}

#[derive(Debug)]
struct PreflightAudit {
    runtime: RuntimeAuthority,
    environment: Vec<(String, String)>,
    console_creation_mode: ConsoleCreationMode,
    binary: Option<NtfsFileIdentity>,
    stdin: Option<CreatedArtifact>,
    prompt: Option<CreatedArtifact>,
    schema: Option<CreatedArtifact>,
    stdout: Option<CreatedArtifact>,
    stderr: Option<CreatedArtifact>,
    child_created: bool,
    child_resumed: bool,
    descendants_zero: bool,
    exit_code: Option<u32>,
    timed_out: bool,
    stdout_total: usize,
    stderr_total: usize,
    stdout_overflow: bool,
    stderr_overflow: bool,
    total_processes: Option<u32>,
    active_processes: Option<u32>,
    pre_resume_total_processes: Option<u32>,
    pre_resume_active_processes: Option<u32>,
    max_observed_total_processes: Option<u32>,
    max_observed_active_processes: Option<u32>,
    child_process_policy_readback: bool,
    child_process_policy_flags: Option<u32>,
}

impl PreflightAudit {
    fn new(
        runtime: RuntimeAuthority,
        environment: Vec<(String, String)>,
        console_creation_mode: ConsoleCreationMode,
    ) -> Self {
        Self {
            runtime,
            environment,
            console_creation_mode,
            binary: None,
            stdin: None,
            prompt: None,
            schema: None,
            stdout: None,
            stderr: None,
            child_created: false,
            child_resumed: false,
            descendants_zero: false,
            exit_code: None,
            timed_out: false,
            stdout_total: 0,
            stderr_total: 0,
            stdout_overflow: false,
            stderr_overflow: false,
            total_processes: None,
            active_processes: None,
            pre_resume_total_processes: None,
            pre_resume_active_processes: None,
            max_observed_total_processes: None,
            max_observed_active_processes: None,
            child_process_policy_readback: false,
            child_process_policy_flags: None,
        }
    }
}

fn run_bounded_diagnostic_suite() -> NativePreflightDiagnosticResult {
    let maintenance = match MaintenanceMutex::acquire() {
        Ok(value) => value,
        Err(blocker) => return diagnostic_suite_failure(blocker.code, Vec::new()),
    };
    let binary = match PinnedBinary::open() {
        Ok(value) => value,
        Err(blocker) => return diagnostic_suite_failure(blocker.code, Vec::new()),
    };
    let mut variants = Vec::with_capacity(DIAGNOSTIC_POLICIES.len());
    for (index, policy) in DIAGNOSTIC_POLICIES.iter().copied().enumerate() {
        let runtime = match RuntimeAuthority::create_diagnostic(index) {
            Ok(value) => value,
            Err(blocker) => {
                variants.push(diagnostic_variant_without_receipt(policy, blocker.code));
                continue;
            }
        };
        let environment = match build_environment_entries(&runtime) {
            Ok(value) => value,
            Err(blocker) => {
                variants.push(diagnostic_variant_for_runtime_failure(
                    policy,
                    &runtime,
                    blocker.code,
                ));
                continue;
            }
        };
        let mut audit =
            PreflightAudit::new(runtime, environment, ConsoleCreationMode::CreateNoWindow);
        let blocker = match execute_probe(
            &mut audit,
            &binary,
            policy,
            ConsoleCreationMode::CreateNoWindow,
            DIAGNOSTIC_VARIANT_DEADLINE,
        ) {
            Ok(outcome) => diagnostic_outcome_blocker(&outcome),
            Err(blocker) => blocker.code,
        };
        variants.push(persist_diagnostic_variant(&audit, policy, blocker));
    }
    drop(binary);
    let suite_blocker = match maintenance.release() {
        Ok(()) => "DIAGNOSTIC_ONLY_NEVER_QUALIFIES",
        Err(_) => "DIAGNOSTIC_MAINTENANCE_RELEASE_FAILED",
    };
    persist_diagnostic_summary(suite_blocker, variants)
}

fn diagnostic_suite_failure(
    blocker: &'static str,
    variants: Vec<NativePreflightDiagnosticVariantResult>,
) -> NativePreflightDiagnosticResult {
    NativePreflightDiagnosticResult {
        exit_code: 3,
        terminal: "BLOCKED_DIAGNOSTIC",
        blocker,
        production: false,
        live: false,
        auth_isolation: false,
        network_enforcement: false,
        network_usage: "UNOBSERVED",
        credential_access: "UNOBSERVED",
        finding: "INCOMPLETE_DIAGNOSTIC_EVIDENCE",
        decision_required: true,
        receipt_path: None,
        receipt_sha256: None,
        variants,
    }
}

fn diagnostic_variant_without_receipt(
    policy: LaunchPolicy,
    blocker: &'static str,
) -> NativePreflightDiagnosticVariantResult {
    NativePreflightDiagnosticVariantResult {
        id: policy.id,
        terminal: "BLOCKED_DIAGNOSTIC",
        blocker,
        production: false,
        live: false,
        auth_isolation: false,
        network_enforcement: false,
        network_usage: "UNOBSERVED",
        credential_access: "UNOBSERVED",
        atomic_job_list: policy.atomic_job_list,
        child_process_restricted: policy.child_process_restricted,
        assigned_after_create: policy.assigned_after_create,
        run_root: None,
        receipt_path: None,
        receipt_sha256: None,
        child_created: false,
        child_resumed: false,
        descendants_zero: false,
        exit_code: None,
        stdout_sha256: None,
        stderr_sha256: None,
        stdout_total_bytes: 0,
        stderr_total_bytes: 0,
        stdout_overflow: false,
        stderr_overflow: false,
        pre_resume_total_processes: None,
        pre_resume_active_processes: None,
        final_total_processes: None,
        final_active_processes: None,
        max_observed_total_processes: None,
        max_observed_active_processes: None,
        max_additional_job_processes_seen: None,
    }
}

fn diagnostic_variant_for_runtime_failure(
    policy: LaunchPolicy,
    runtime: &RuntimeAuthority,
    blocker: &'static str,
) -> NativePreflightDiagnosticVariantResult {
    let mut result = diagnostic_variant_without_receipt(policy, blocker);
    result.run_root = Some(runtime.run_root.clone());
    result
}

fn diagnostic_variant_from_audit(
    audit: &PreflightAudit,
    policy: LaunchPolicy,
    blocker: &'static str,
) -> NativePreflightDiagnosticVariantResult {
    let mut result = diagnostic_variant_for_runtime_failure(policy, &audit.runtime, blocker);
    result.child_created = audit.child_created;
    result.child_resumed = audit.child_resumed;
    result.descendants_zero = audit.descendants_zero;
    result.exit_code = audit.exit_code;
    result.stdout_sha256 = audit
        .stdout
        .as_ref()
        .map(|artifact| artifact.identity.sha256.clone());
    result.stderr_sha256 = audit
        .stderr
        .as_ref()
        .map(|artifact| artifact.identity.sha256.clone());
    result.stdout_total_bytes = audit.stdout_total;
    result.stderr_total_bytes = audit.stderr_total;
    result.stdout_overflow = audit.stdout_overflow;
    result.stderr_overflow = audit.stderr_overflow;
    result.pre_resume_total_processes = audit.pre_resume_total_processes;
    result.pre_resume_active_processes = audit.pre_resume_active_processes;
    result.final_total_processes = audit.total_processes;
    result.final_active_processes = audit.active_processes;
    result.max_observed_total_processes = audit.max_observed_total_processes;
    result.max_observed_active_processes = audit.max_observed_active_processes;
    result.max_additional_job_processes_seen = audit
        .max_observed_total_processes
        .map(|processes| processes.saturating_sub(1));
    result
}

fn validate_internal_diagnostic_variant(
    result: &NativePreflightDiagnosticVariantResult,
) -> Result<(), Blocker> {
    if result.terminal != "BLOCKED_DIAGNOSTIC"
        || result.production
        || result.live
        || result.auth_isolation
        || result.network_enforcement
        || result.network_usage != "UNOBSERVED"
        || result.credential_access != "UNOBSERVED"
    {
        return Err(Blocker::new(
            Gate::Manifest,
            "DIAGNOSTIC_FAIL_CLOSED_CONTRACT_INVALID",
        ));
    }
    Ok(())
}

fn validate_internal_diagnostic_summary(
    result: &NativePreflightDiagnosticResult,
) -> Result<(), Blocker> {
    if result.exit_code != 3
        || result.terminal != "BLOCKED_DIAGNOSTIC"
        || result.production
        || result.live
        || result.auth_isolation
        || result.network_enforcement
        || result.network_usage != "UNOBSERVED"
        || result.credential_access != "UNOBSERVED"
        || !result.decision_required
    {
        return Err(Blocker::new(
            Gate::Manifest,
            "DIAGNOSTIC_SUMMARY_FAIL_CLOSED_CONTRACT_INVALID",
        ));
    }
    Ok(())
}

fn persist_diagnostic_variant(
    audit: &PreflightAudit,
    policy: LaunchPolicy,
    blocker: &'static str,
) -> NativePreflightDiagnosticVariantResult {
    let mut result = diagnostic_variant_from_audit(audit, policy, blocker);
    if validate_internal_diagnostic_variant(&result).is_err() {
        result.blocker = "DIAGNOSTIC_FAIL_CLOSED_CONTRACT_INVALID";
        return result;
    }
    let receipt_path = audit.runtime.run_root.join("diagnostic-receipt.json");
    let receipt_bytes = diagnostic_variant_receipt_bytes(audit, &result);
    match create_new_artifact(
        &receipt_path,
        &audit.runtime.run_root_directory,
        &receipt_bytes,
        Gate::CleanupFinalized,
    ) {
        Ok(receipt) => {
            result.receipt_path = Some(receipt.identity.path.clone());
            result.receipt_sha256 = Some(receipt.identity.sha256.clone());
        }
        Err(_) => result.blocker = "DIAGNOSTIC_RECEIPT_CREATE_NEW_FAILED",
    }
    result
}

fn diagnostic_outcome_blocker(outcome: &ChildOutcome) -> &'static str {
    if outcome.timed_out {
        return "DIAGNOSTIC_DEADLINE_EXCEEDED";
    }
    if outcome.stdout.overflow || outcome.stderr.overflow {
        return "DIAGNOSTIC_OUTPUT_LIMIT_EXCEEDED";
    }
    if outcome.exit_code == STATUS_DLL_INIT_FAILED
        && outcome.stdout.total_bytes == 0
        && outcome.stderr.total_bytes == 0
    {
        return "CODEX_0147_DLL_INIT_FAILED_UNDER_VARIANT";
    }
    if outcome.exit_code != 0 {
        return "DIAGNOSTIC_EXIT_NONZERO";
    }
    let Ok(stdout) = std::str::from_utf8(&outcome.stdout.bytes) else {
        return "DIAGNOSTIC_STDOUT_NOT_UTF8";
    };
    let Ok(stderr) = std::str::from_utf8(&outcome.stderr.bytes) else {
        return "DIAGNOSTIC_STDERR_NOT_UTF8";
    };
    if stdout == format!("{EXPECTED_CODEX_VERSION}\n") && stderr.is_empty() {
        "DIAGNOSTIC_VERSION_OUTPUT_OBSERVED"
    } else {
        "DIAGNOSTIC_VERSION_OUTPUT_MISMATCH"
    }
}

fn diagnostic_variant_receipt_bytes(
    audit: &PreflightAudit,
    result: &NativePreflightDiagnosticVariantResult,
) -> Vec<u8> {
    let environment = audit
        .environment
        .iter()
        .map(|(key, value)| format!("\"{}\":\"{}\"", json_escape(key), json_escape(value)))
        .collect::<Vec<_>>()
        .join(",");
    format!(
        "{{\"schema\":\"pkm.codex.preflight-diagnostic.variant.v1\",\"terminal\":\"BLOCKED_DIAGNOSTIC\",\"production\":false,\"live\":false,\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"auth_isolation\":false,\"network_enforcement\":false,\"network_usage\":\"UNOBSERVED\",\"credential_access\":\"UNOBSERVED\",\"variant\":{},\"binary\":{},\"argv\":[\"--version\"],\"environment\":{{{}}},\"stdin\":{},\"prompt\":{},\"schema_artifact\":{},\"stdout\":{},\"stderr\":{},\"job_before_resume\":true,\"job_active_process_limit\":1,\"deadline_seconds\":{},\"process_trace\":\"JOB_ACCOUNTING_ONLY\",\"loader_trace\":\"NOT_CAPTURED\"}}\n",
        result.to_json(),
        identity_json(audit.binary.as_ref()),
        environment,
        identity_json(audit.stdin.as_ref().map(|value| &value.identity)),
        identity_json(audit.prompt.as_ref().map(|value| &value.identity)),
        identity_json(audit.schema.as_ref().map(|value| &value.identity)),
        identity_json(audit.stdout.as_ref().map(|value| &value.identity)),
        identity_json(audit.stderr.as_ref().map(|value| &value.identity)),
        DIAGNOSTIC_VARIANT_DEADLINE.as_secs(),
    )
    .into_bytes()
}

fn persist_diagnostic_summary(
    blocker: &'static str,
    variants: Vec<NativePreflightDiagnosticVariantResult>,
) -> NativePreflightDiagnosticResult {
    let mut result = diagnostic_suite_failure(blocker, variants);
    if result.variants.iter().any(|variant| {
        validate_internal_diagnostic_variant(variant).is_err()
            || variant.receipt_path.is_none()
            || variant.receipt_sha256.is_none()
            || !variant.child_created
            || !variant.child_resumed
            || !variant.descendants_zero
            || variant.exit_code.is_none()
            || variant.stdout_sha256.is_none()
            || variant.stderr_sha256.is_none()
            || variant.stdout_overflow
            || variant.stderr_overflow
    }) {
        result.blocker = "DIAGNOSTIC_VARIANT_EVIDENCE_INCOMPLETE";
    }
    result.finding = derive_diagnostic_matrix_finding(&result.variants);
    if validate_internal_diagnostic_summary(&result).is_err() {
        result.blocker = "DIAGNOSTIC_SUMMARY_FAIL_CLOSED_CONTRACT_INVALID";
        result.finding = "INCOMPLETE_DIAGNOSTIC_EVIDENCE";
    }
    let runtime = match RuntimeAuthority::create_diagnostic_summary() {
        Ok(value) => value,
        Err(_) => {
            result.blocker = "DIAGNOSTIC_SUMMARY_ROOT_CREATE_FAILED";
            return result;
        }
    };
    let variants_json = result
        .variants
        .iter()
        .map(NativePreflightDiagnosticVariantResult::to_json)
        .collect::<Vec<_>>()
        .join(",");
    let summary_bytes = format!(
        "{{\"schema\":\"pkm.codex.preflight-diagnostic.summary.v1\",\"terminal\":\"BLOCKED_DIAGNOSTIC\",\"blocker\":\"{}\",\"production\":false,\"live\":false,\"auth_isolation\":false,\"network_enforcement\":false,\"network_usage\":\"UNOBSERVED\",\"credential_access\":\"UNOBSERVED\",\"finding\":\"{}\",\"decision_required\":true,\"variant_order\":[\"A\",\"B\",\"C\",\"D\"],\"variants\":[{}]}}\n",
        json_escape(result.blocker),
        json_escape(result.finding),
        variants_json,
    )
    .into_bytes();
    let receipt_path = runtime.run_root.join("diagnostic-summary.json");
    match create_new_artifact(
        &receipt_path,
        &runtime.run_root_directory,
        &summary_bytes,
        Gate::CleanupFinalized,
    ) {
        Ok(receipt) => {
            result.receipt_path = Some(receipt.identity.path.clone());
            result.receipt_sha256 = Some(receipt.identity.sha256.clone());
        }
        Err(_) => result.blocker = "DIAGNOSTIC_SUMMARY_RECEIPT_CREATE_NEW_FAILED",
    }
    result
}

fn derive_diagnostic_matrix_finding(
    variants: &[NativePreflightDiagnosticVariantResult],
) -> &'static str {
    if variants.len() != 4 {
        return "INCOMPLETE_DIAGNOSTIC_EVIDENCE";
    }
    let expected_ids = ["A", "B", "C", "D"];
    if variants
        .iter()
        .zip(expected_ids)
        .any(|(variant, id)| variant.id != id || !variant.descendants_zero)
    {
        return "INCOMPLETE_DIAGNOSTIC_EVIDENCE";
    }
    let restricted_match = [&variants[0], &variants[2]].iter().all(|variant| {
        variant.child_process_restricted
            && variant.exit_code == Some(STATUS_DLL_INIT_FAILED)
            && variant.max_additional_job_processes_seen == Some(0)
            && variant.blocker == "CODEX_0147_DLL_INIT_FAILED_UNDER_VARIANT"
    });
    let unrestricted_match = [&variants[1], &variants[3]].iter().all(|variant| {
        !variant.child_process_restricted
            && variant.exit_code == Some(0)
            && variant.max_additional_job_processes_seen == Some(1)
            && variant.blocker == "DIAGNOSTIC_VERSION_OUTPUT_OBSERVED"
    });
    if restricted_match && unrestricted_match {
        "CHILD_PROCESS_RESTRICTION_TRUE_OBSERVED_C0000142_WITH_ZERO_ADDITIONAL_JOB_PROCESSES_FALSE_OBSERVED_EXIT0_WITH_ONE_ADDITIONAL_JOB_PROCESS_IN_BOTH_JOB_ASSIGNMENT_MODES"
    } else {
        "DIAGNOSTIC_MATRIX_DID_NOT_MATCH_FROZEN_EXPECTATION"
    }
}

pub fn run_native_preflight() -> NativePreflightResult {
    let runtime = match RuntimeAuthority::create() {
        Ok(value) => value,
        Err(blocker) => return result_without_receipt(blocker),
    };
    let environment = match build_environment_entries(&runtime) {
        Ok(value) => value,
        Err(blocker) => return result_for_runtime_without_receipt(runtime, blocker),
    };
    let mut audit = PreflightAudit::new(runtime, environment, ConsoleCreationMode::DetachedProcess);
    let attempt = execute_preflight(&mut audit);
    let terminal = if attempt.is_ok() {
        "PREFLIGHT_ONLY_PASSED"
    } else {
        "BLOCKED_PREFLIGHT"
    };
    let blocker = attempt.as_ref().err().map(|value| value.code);
    let receipt_bytes = receipt_bytes(&audit, terminal, blocker);
    let receipt_path = audit.runtime.run_root.join("preflight-receipt.json");
    let receipt = create_new_artifact(
        &receipt_path,
        &audit.runtime.run_root_directory,
        &receipt_bytes,
        Gate::CleanupFinalized,
    );
    match receipt {
        Ok(receipt) => NativePreflightResult {
            exit_code: if attempt.is_ok() { 0 } else { 3 },
            terminal,
            blocker,
            run_root: Some(audit.runtime.run_root.clone()),
            receipt_path: Some(receipt.identity.path.clone()),
            receipt_sha256: Some(receipt.identity.sha256.clone()),
            child_created: audit.child_created,
            child_resumed: audit.child_resumed,
            descendants_zero: audit.descendants_zero,
        },
        Err(_) => NativePreflightResult {
            exit_code: 10,
            terminal: "BLOCKED_PREFLIGHT",
            blocker: Some("PREFLIGHT_RECEIPT_CREATE_NEW_FAILED"),
            run_root: Some(audit.runtime.run_root.clone()),
            receipt_path: None,
            receipt_sha256: None,
            child_created: audit.child_created,
            child_resumed: audit.child_resumed,
            descendants_zero: audit.descendants_zero,
        },
    }
}

pub fn run_native_child_policy_diagnostic() -> NativeChildPolicyDiagnosticResult {
    let mut result = NativeChildPolicyDiagnosticResult {
        exit_code: 3,
        terminal: "BLOCKED_CHILD_POLICY_DIAGNOSTIC",
        blocker: "CHILD_POLICY_DIAGNOSTIC_EVIDENCE_INCOMPLETE",
        production: false,
        live: false,
        run_root: None,
        receipt_path: None,
        receipt_sha256: None,
        child_process_policy_readback: false,
        child_process_policy_flags: None,
        canary_child_creation_attempted: false,
        canary_child_creation_blocked: false,
        job_total_processes: None,
        job_active_processes: None,
        descendants_zero: false,
    };
    let maintenance = match MaintenanceMutex::acquire() {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    let runtime = match RuntimeAuthority::create_with_prefix(CHILD_POLICY_RUN_PREFIX) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.run_root = Some(runtime.run_root.clone());
    let environment = match build_environment_entries(&runtime) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    let qualifier_path = match std::env::current_exe() {
        Ok(value) => value,
        Err(_) => {
            result.blocker = "CHILD_POLICY_QUALIFIER_PATH_UNAVAILABLE";
            return result;
        }
    };
    let canary_path = match qualifier_path.parent() {
        Some(parent) => parent.join("pkm-child-policy-canary.exe"),
        None => {
            result.blocker = "CHILD_POLICY_CANARY_PARENT_UNAVAILABLE";
            return result;
        }
    };
    if std::fs::symlink_metadata(&canary_path)
        .map(|metadata| metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0)
        .unwrap_or(true)
    {
        result.blocker = "CHILD_POLICY_CANARY_REPARSE_OR_MISSING";
        return result;
    }
    let canary_file = match OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&canary_path)
    {
        Ok(value) => value,
        Err(_) => {
            result.blocker = "CHILD_POLICY_CANARY_OPEN_FAILED";
            return result;
        }
    };
    let (canary_volume, canary_file_id, canary_links, canary_length) =
        match file_id_from_handle(&canary_file, Gate::BinaryIdentity) {
            Ok(value) => value,
            Err(error) => {
                result.blocker = error.code;
                return result;
            }
        };
    let canary_sha256 = match sha256_open_file(&canary_file, Gate::BinaryIdentity) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    // Cargo hard-links the top-level debug binary into deps. A release-bound
    // candidate must later be copied create-new with link_count=1; this
    // diagnostic records release_bound=false and accepts only Cargo's exact
    // one- or two-link development shapes.
    if !(1..=2).contains(&canary_links) || canary_length == 0 {
        result.blocker = "CHILD_POLICY_CANARY_IDENTITY_INVALID";
        return result;
    }

    let command_line = format!("\"{}\"", canary_path.display());
    let mut child = match ChildTransaction::create(
        &canary_path,
        OsStr::new(&command_line),
        &runtime,
        &environment,
        CANONICAL_PREFLIGHT_POLICY,
        ConsoleCreationMode::DetachedProcess,
        CHILD_POLICY_CANARY_DEADLINE,
    ) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.child_process_policy_readback = true;
    result.child_process_policy_flags = Some(child.child_process_policy_flags);
    if let Err(error) = child.prepare_job_membership(CANONICAL_PREFLIGHT_POLICY) {
        result.descendants_zero = child.drained;
        result.blocker = error.code;
        return result;
    }
    let outcome = match child.run() {
        Ok(value) => value,
        Err(error) => {
            result.descendants_zero = child.drained;
            result.blocker = error.code;
            return result;
        }
    };
    result.job_total_processes = Some(outcome.total_processes);
    result.job_active_processes = Some(outcome.active_processes);
    result.descendants_zero = child.drained;
    let stdout_artifact = match create_new_artifact(
        &runtime.output.join("child-policy-stdout.raw"),
        &runtime.output_directory,
        &outcome.stdout.bytes,
        Gate::OutputAndDeadline,
    ) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    let stderr_artifact = match create_new_artifact(
        &runtime.output.join("child-policy-stderr.raw"),
        &runtime.output_directory,
        &outcome.stderr.bytes,
        Gate::OutputAndDeadline,
    ) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    let stdout = match std::str::from_utf8(&outcome.stdout.bytes) {
        Ok(value) => value,
        Err(_) => {
            result.blocker = "CHILD_POLICY_CANARY_STDOUT_NOT_UTF8";
            return result;
        }
    };
    if outcome.exit_code != 0
        || outcome.timed_out
        || outcome.stdout.overflow
        || outcome.stderr.overflow
        || outcome.total_processes != 1
        || outcome.active_processes != 0
        || child.child_process_policy_flags != PROCESS_CREATION_CHILD_PROCESS_RESTRICTED
        || stdout != EXPECTED_CHILD_POLICY_CANARY_STDOUT
        || !outcome.stderr.bytes.is_empty()
        || !child.drained
    {
        result.blocker = "CHILD_POLICY_NEGATIVE_CANARY_MISMATCH";
        return result;
    }

    result.canary_child_creation_attempted = true;
    result.canary_child_creation_blocked = true;
    result.blocker = "CHILD_PROCESS_RESTRICTION_NEGATIVE_CANARY_OBSERVED_NOT_RELEASE_BOUND";
    let receipt_bytes = format!(
        "{{\"schema\":\"pkm.codex.child-policy-diagnostic.receipt.v1\",\"terminal\":\"BLOCKED_CHILD_POLICY_DIAGNOSTIC\",\"blocker\":\"{}\",\"production\":false,\"live\":false,\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"network_usage\":\"UNOBSERVED\",\"credential_access\":\"UNOBSERVED\",\"run_id\":\"{}\",\"run_root\":\"{}\",\"canary_binary\":{{\"path\":\"{}\",\"volume_serial\":{},\"file_id\":\"{}\",\"link_count\":{},\"length\":{},\"sha256\":\"{}\",\"release_bound\":false}},\"argv\":[],\"console_creation_mode\":\"DETACHED_PROCESS\",\"child_process_restricted\":true,\"child_process_policy_readback\":true,\"child_process_policy_flags\":{},\"canary_child_creation_attempted\":true,\"canary_child_creation_blocked\":true,\"canary_win32_error\":367,\"exit_code\":{},\"stdout\":{},\"stderr\":{},\"job_total_processes\":{},\"job_active_processes\":{},\"descendants_zero\":true}}\n",
        result.blocker,
        runtime.run_id,
        json_escape(&runtime.run_root.to_string_lossy()),
        json_escape(&canary_path.to_string_lossy()),
        canary_volume,
        hex_bytes(&canary_file_id),
        canary_links,
        canary_length,
        canary_sha256,
        child.child_process_policy_flags,
        outcome.exit_code,
        identity_json(Some(&stdout_artifact.identity)),
        identity_json(Some(&stderr_artifact.identity)),
        outcome.total_processes,
        outcome.active_processes,
    )
    .into_bytes();
    let receipt = match create_new_artifact(
        &runtime
            .run_root
            .join("child-policy-diagnostic-receipt.json"),
        &runtime.run_root_directory,
        &receipt_bytes,
        Gate::CleanupFinalized,
    ) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    if maintenance.release().is_err() {
        result.blocker = "CHILD_POLICY_MAINTENANCE_RELEASE_FAILED";
        return result;
    }
    result.receipt_path = Some(receipt.identity.path.clone());
    result.receipt_sha256 = Some(receipt.identity.sha256.clone());
    result
}

pub fn run_native_appcontainer_profile_diagnostic() -> NativeAppContainerProfileDiagnosticResult {
    let mut result = NativeAppContainerProfileDiagnosticResult {
        exit_code: 3,
        terminal: "BLOCKED_APPCONTAINER_PROFILE_DIAGNOSTIC",
        blocker: "APPCONTAINER_PROFILE_DIAGNOSTIC_EVIDENCE_INCOMPLETE",
        production: false,
        live: false,
        run_root: None,
        receipt_path: None,
        receipt_sha256: None,
        profile_name_sha256: None,
        package_sid_sha256: None,
        zero_capabilities: true,
        first_create: false,
        derived_sid_match: false,
        first_delete: false,
        recreate: false,
        sid_stable: false,
        final_delete: false,
    };
    let maintenance = match MaintenanceMutex::acquire() {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    let runtime = match RuntimeAuthority::create_with_prefix(APPCONTAINER_PROFILE_RUN_PREFIX) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.run_root = Some(runtime.run_root.clone());
    let profile_name = format!("OpenAI.PKMWorkflow.Codex.T13b.{}", runtime.run_id);
    let profile_name_sha256 = match crate::sha256_digest(profile_name.as_bytes()) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.profile_name_sha256 = Some(profile_name_sha256.clone());

    let first = match create_zero_capability_profile(&profile_name) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.first_create = true;
    let derived = match derive_appcontainer_sid(&profile_name) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "APPCONTAINER_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    if !appcontainer_sids_equal(&first, &derived) {
        result.blocker = match delete_profile(&profile_name) {
            Ok(()) => "APPCONTAINER_DERIVED_SID_MISMATCH",
            Err(_) => "APPCONTAINER_PROFILE_ROLLBACK_FAILED",
        };
        return result;
    }
    result.derived_sid_match = true;
    let first_sid_bytes = match appcontainer_sid_bytes(&first) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "APPCONTAINER_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    let package_sid_sha256 = match crate::sha256_digest(&first_sid_bytes) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "APPCONTAINER_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    result.package_sid_sha256 = Some(package_sid_sha256.clone());
    if let Err(error) = delete_profile(&profile_name) {
        result.blocker = error.code;
        return result;
    }
    result.first_delete = true;
    drop(derived);
    drop(first);

    let second = match create_zero_capability_profile(&profile_name) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.recreate = true;
    let second_sid_bytes = match appcontainer_sid_bytes(&second) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "APPCONTAINER_PROFILE_FINAL_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    let second_sid_sha256 = match crate::sha256_digest(&second_sid_bytes) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "APPCONTAINER_PROFILE_FINAL_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    result.sid_stable = second_sid_sha256 == package_sid_sha256;
    if !result.sid_stable {
        result.blocker = match delete_profile(&profile_name) {
            Ok(()) => "APPCONTAINER_SID_NOT_STABLE",
            Err(_) => "APPCONTAINER_PROFILE_FINAL_ROLLBACK_FAILED",
        };
        return result;
    }
    if let Err(error) = delete_profile(&profile_name) {
        result.blocker = error.code;
        return result;
    }
    result.final_delete = true;
    drop(second);
    if maintenance.release().is_err() {
        result.blocker = "APPCONTAINER_MAINTENANCE_RELEASE_FAILED";
        return result;
    }
    result.blocker = "APPCONTAINER_PROFILE_OBSERVED_NOT_RELEASE_BOUND";
    let receipt_bytes = format!(
        "{{\"schema\":\"pkm.codex.appcontainer-profile-diagnostic.receipt.v1\",\"terminal\":\"BLOCKED_APPCONTAINER_PROFILE_DIAGNOSTIC\",\"blocker\":\"{}\",\"production\":false,\"live\":false,\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"run_id\":\"{}\",\"run_root\":\"{}\",\"profile_name_sha256\":\"{}\",\"package_sid_sha256\":\"{}\",\"zero_capabilities\":true,\"first_create\":true,\"derived_sid_match\":true,\"first_delete\":true,\"recreate\":true,\"sid_stable\":true,\"final_delete\":true}}\n",
        result.blocker,
        runtime.run_id,
        json_escape(&runtime.run_root.to_string_lossy()),
        profile_name_sha256,
        package_sid_sha256,
    )
    .into_bytes();
    let receipt = match create_new_artifact(
        &runtime
            .run_root
            .join("appcontainer-profile-diagnostic-receipt.json"),
        &runtime.run_root_directory,
        &receipt_bytes,
        Gate::CleanupFinalized,
    ) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.receipt_path = Some(receipt.identity.path.clone());
    result.receipt_sha256 = Some(receipt.identity.sha256.clone());
    result
}

pub fn run_native_lpac_token_diagnostic() -> NativeLpacTokenDiagnosticResult {
    let mut result = NativeLpacTokenDiagnosticResult {
        exit_code: 3,
        terminal: "BLOCKED_LPAC_TOKEN_DIAGNOSTIC",
        blocker: "LPAC_TOKEN_DIAGNOSTIC_EVIDENCE_INCOMPLETE",
        production: false,
        live: false,
        run_root: None,
        receipt_path: None,
        receipt_sha256: None,
        package_sid_sha256: None,
        canary_sha256: None,
        profile_created: false,
        child_created_suspended: false,
        create_win32_error: None,
        child_resumed: false,
        lpac_attribute_accepted: false,
        job_membership_verified: false,
        child_process_restricted: false,
        token_is_appcontainer: false,
        token_is_lpac: None,
        lpac_direct_query_supported: false,
        capability_count: None,
        package_sid_match: false,
        descendants_zero: false,
        profile_deleted: false,
    };
    let maintenance = match MaintenanceMutex::acquire() {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    let runtime = match RuntimeAuthority::create_with_prefix(LPAC_TOKEN_RUN_PREFIX) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.run_root = Some(runtime.run_root.clone());
    let profile_name = format!("OpenAI.PKMWorkflow.Codex.T13b.{}", runtime.run_id);
    let sid = match create_zero_capability_profile(&profile_name) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.profile_created = true;
    let sid_bytes = match appcontainer_sid_bytes(&sid) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    let sid_sha256 = match crate::sha256_digest(&sid_bytes) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    result.package_sid_sha256 = Some(sid_sha256.clone());
    let environment = match build_lpac_environment_entries(&runtime, sid.0) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    let qualifier_path = match std::env::current_exe() {
        Ok(value) => value,
        Err(_) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => "LPAC_QUALIFIER_PATH_UNAVAILABLE",
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    let canary_path = match qualifier_path.parent() {
        Some(parent) => parent.join("pkm-child-policy-canary.exe"),
        None => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => "LPAC_CANARY_PARENT_UNAVAILABLE",
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    if std::fs::symlink_metadata(&canary_path)
        .map(|metadata| metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0)
        .unwrap_or(true)
    {
        result.blocker = match delete_profile(&profile_name) {
            Ok(()) => "LPAC_CANARY_REPARSE_OR_MISSING",
            Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
        };
        return result;
    }
    let canary_file = match OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&canary_path)
    {
        Ok(value) => value,
        Err(_) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => "LPAC_CANARY_OPEN_FAILED",
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    let canary_identity = match file_id_from_handle(&canary_file, Gate::BinaryIdentity) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    let canary_sha256 = match sha256_open_file(&canary_file, Gate::BinaryIdentity) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    if !(1..=2).contains(&canary_identity.2) || canary_identity.3 == 0 {
        result.blocker = match delete_profile(&profile_name) {
            Ok(()) => "LPAC_CANARY_IDENTITY_INVALID",
            Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
        };
        return result;
    }
    if let Err(error) = verify_pe_file(&canary_file) {
        result.blocker = match delete_profile(&profile_name) {
            Ok(()) => error.code,
            Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
        };
        return result;
    }
    result.canary_sha256 = Some(canary_sha256.clone());

    let mut child = match SuspendedLpacChild::create(
        &canary_path,
        &runtime,
        &environment,
        sid.0,
        &mut result.create_win32_error,
    ) {
        Ok(value) => value,
        Err(failure) => {
            result.child_created_suspended = failure.child_created_suspended;
            result.lpac_attribute_accepted = failure.lpac_attribute_accepted;
            result.descendants_zero = failure.descendants_zero;
            if !failure.profile_cleanup_allowed() {
                drop(sid);
                result.blocker = failure.blocker.code;
                return result;
            }
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => failure.blocker.code,
                Err(_) => "LPAC_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    result.child_created_suspended = true;
    result.lpac_attribute_accepted = true;
    result.job_membership_verified = true;
    result.child_process_restricted = child.child_process_policy_flags == 1;

    let reopened_matches = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&canary_path)
        .and_then(|file| {
            let identity = file_id_from_handle(&file, Gate::BinaryIdentity)
                .map_err(|_| std::io::Error::other("identity"))?;
            let sha256 = sha256_open_file(&file, Gate::BinaryIdentity)
                .map_err(|_| std::io::Error::other("hash"))?;
            Ok(identity == canary_identity && sha256 == canary_sha256)
        })
        .unwrap_or(false);
    let observation = child.observe_token(sid.0);
    if let Ok(value) = observation.as_ref() {
        result.token_is_appcontainer = value.is_appcontainer;
        result.token_is_lpac = value.is_lpac;
        result.lpac_direct_query_supported = value.direct_lpac_query_supported;
        result.capability_count = Some(value.capability_count);
        result.package_sid_match = value.package_sid_match;
    }
    let drain = child.terminate_and_drain();
    result.descendants_zero = child.drained;
    drop(child);
    if let Err(error) = drain {
        // Without an exact zero-descendant proof, retain the restrictive
        // AppContainer profile for operator reconciliation. Drop may retry
        // Job termination, but a best-effort retry is not qualification proof.
        drop(sid);
        result.blocker = error.code;
        return result;
    }
    let profile_delete = delete_profile(&profile_name);
    if profile_delete.is_ok() {
        result.profile_deleted = true;
    }
    drop(sid);
    let maintenance_release = maintenance.release();

    if profile_delete.is_err() {
        result.blocker = "LPAC_PROFILE_FINAL_DELETE_FAILED";
        return result;
    }
    if maintenance_release.is_err() {
        result.blocker = "LPAC_MAINTENANCE_RELEASE_FAILED";
        return result;
    }
    let observation = match observation {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    if !reopened_matches
        || !observation.is_appcontainer
        || observation.capability_count != 0
        || !observation.package_sid_match
        || !result.child_process_restricted
        || !result.descendants_zero
    {
        result.blocker = "LPAC_TOKEN_READBACK_MISMATCH";
        return result;
    }
    if observation.direct_lpac_query_supported && observation.is_lpac != Some(true) {
        result.blocker = "LPAC_DIRECT_TOKEN_READBACK_MISMATCH";
        return result;
    }

    result.blocker = if observation.direct_lpac_query_supported {
        "LPAC_TOKEN_OBSERVED_NOT_RELEASE_BOUND"
    } else {
        "LPAC_DIRECT_TOKEN_READBACK_UNSUPPORTED"
    };
    let receipt_bytes = format!(
        "{{\"schema\":\"pkm.codex.lpac-token-diagnostic.receipt.v1\",\"terminal\":\"BLOCKED_LPAC_TOKEN_DIAGNOSTIC\",\"blocker\":\"{}\",\"production\":false,\"live\":false,\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"run_id\":\"{}\",\"run_root\":\"{}\",\"package_sid_sha256\":\"{}\",\"canary_sha256\":\"{}\",\"canary_release_bound\":false,\"profile_created\":true,\"child_created_suspended\":true,\"child_resumed\":false,\"lpac_attribute_accepted\":true,\"job_membership_verified\":true,\"child_process_restricted\":true,\"token_is_appcontainer\":true,\"token_is_lpac\":{},\"lpac_direct_query_supported\":{},\"capability_count\":0,\"package_sid_match\":true,\"descendants_zero\":true,\"profile_deleted\":true}}\n",
        result.blocker,
        runtime.run_id,
        json_escape(&runtime.run_root.to_string_lossy()),
        sid_sha256,
        canary_sha256,
        json_bool_or_null(result.token_is_lpac),
        json_bool(result.lpac_direct_query_supported),
    )
    .into_bytes();
    let receipt = match create_new_artifact(
        &runtime.run_root.join("lpac-token-diagnostic-receipt.json"),
        &runtime.run_root_directory,
        &receipt_bytes,
        Gate::CleanupFinalized,
    ) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.receipt_path = Some(receipt.identity.path.clone());
    result.receipt_sha256 = Some(receipt.identity.sha256.clone());
    result
}

pub fn run_native_wfp_policy_diagnostic() -> NativeWfpPolicyDiagnosticResult {
    let mut result = NativeWfpPolicyDiagnosticResult {
        exit_code: 3,
        terminal: "BLOCKED_WFP_POLICY_DIAGNOSTIC",
        blocker: "WFP_POLICY_DIAGNOSTIC_EVIDENCE_INCOMPLETE",
        production: false,
        live: false,
        run_root: None,
        receipt_path: None,
        receipt_sha256: None,
        profile_created: false,
        wfp_installed: false,
        filter_readback: false,
        cleanup_verified: false,
        negative_matrix: false,
        package_sid_sha256: None,
        wfp_policy_sha256: None,
    };
    match process_is_elevated() {
        Ok(true) => {}
        Ok(false) => {
            result.blocker = "ELEVATION_REQUIRED";
            return result;
        }
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    }
    let maintenance = match MaintenanceMutex::acquire() {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    let runtime = match RuntimeAuthority::create_with_prefix(WFP_POLICY_RUN_PREFIX) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.run_root = Some(runtime.run_root.clone());
    let profile_name = format!("OpenAI.PKMWorkflow.Codex.T13b.{}", runtime.run_id);
    let sid = match create_zero_capability_profile(&profile_name) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.profile_created = true;
    let sid_bytes = match appcontainer_sid_bytes(&sid) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "WFP_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    let sid_sha256 = match crate::sha256_digest(&sid_bytes) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "WFP_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    result.package_sid_sha256 = Some(sid_sha256.clone());
    let policy_bytes = format!(
        "pkm-wfp-v1|sid={sid_sha256}|permit=ipv4,tcp,127.0.0.1,{}|block=ipv4,all|block=ipv6,all",
        crate::GOLDEN_PROXY_PORT,
    );
    let policy_sha256 = match crate::sha256_digest(policy_bytes.as_bytes()) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "WFP_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    result.wfp_policy_sha256 = Some(policy_sha256.clone());
    let (engine, filter_ids) = match install_wfp_default_deny(sid.0, crate::GOLDEN_PROXY_PORT) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = match delete_profile(&profile_name) {
                Ok(()) => error.code,
                Err(_) => "WFP_PROFILE_ROLLBACK_FAILED",
            };
            return result;
        }
    };
    result.wfp_installed = true;
    result.filter_readback = true;
    drop(engine);
    if let Err(error) = verify_wfp_dynamic_cleanup(filter_ids) {
        result.blocker = match delete_profile(&profile_name) {
            Ok(()) => error.code,
            Err(_) => "WFP_PROFILE_FINAL_ROLLBACK_FAILED",
        };
        return result;
    }
    if let Err(error) = delete_profile(&profile_name) {
        result.blocker = error.code;
        return result;
    }
    drop(sid);
    if maintenance.release().is_err() {
        result.blocker = "WFP_MAINTENANCE_RELEASE_FAILED";
        return result;
    }
    result.cleanup_verified = true;
    result.blocker = "WFP_NEGATIVE_MATRIX_NOT_RUN";
    let receipt_bytes = format!(
        "{{\"schema\":\"pkm.codex.wfp-policy-diagnostic.receipt.v1\",\"terminal\":\"BLOCKED_WFP_POLICY_DIAGNOSTIC\",\"blocker\":\"WFP_NEGATIVE_MATRIX_NOT_RUN\",\"production\":false,\"live\":false,\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"run_id\":\"{}\",\"run_root\":\"{}\",\"package_sid_sha256\":\"{}\",\"wfp_policy_sha256\":\"{}\",\"proxy_port\":{},\"profile_created\":true,\"wfp_installed\":true,\"filter_readback\":true,\"cleanup_verified\":true,\"negative_matrix\":false}}\n",
        runtime.run_id,
        json_escape(&runtime.run_root.to_string_lossy()),
        sid_sha256,
        policy_sha256,
        crate::GOLDEN_PROXY_PORT,
    )
    .into_bytes();
    let receipt = match create_new_artifact(
        &runtime.run_root.join("wfp-policy-diagnostic-receipt.json"),
        &runtime.run_root_directory,
        &receipt_bytes,
        Gate::CleanupFinalized,
    ) {
        Ok(value) => value,
        Err(error) => {
            result.blocker = error.code;
            return result;
        }
    };
    result.receipt_path = Some(receipt.identity.path.clone());
    result.receipt_sha256 = Some(receipt.identity.sha256.clone());
    result
}

fn execute_preflight(audit: &mut PreflightAudit) -> Result<(), Blocker> {
    let maintenance = MaintenanceMutex::acquire()?;
    let binary = PinnedBinary::open()?;
    let outcome = execute_probe(
        audit,
        &binary,
        CANONICAL_PREFLIGHT_POLICY,
        ConsoleCreationMode::DetachedProcess,
        PREFLIGHT_DEADLINE,
    )?;
    validate_outcome(&outcome)?;
    maintenance.release()?;
    Err(Blocker::new(
        Gate::FileBoundary,
        "PREFLIGHT_FULL_TOKEN_AUTH_ISOLATION_NOT_QUALIFIED",
    ))
}

fn execute_probe(
    audit: &mut PreflightAudit,
    binary: &PinnedBinary,
    policy: LaunchPolicy,
    console_creation_mode: ConsoleCreationMode,
    deadline: Duration,
) -> Result<ChildOutcome, Blocker> {
    audit.binary = Some(binary.identity.clone());
    audit.stdin = Some(create_new_artifact(
        &audit.runtime.sealed.join("stdin.bin"),
        &audit.runtime.sealed_directory,
        PREFLIGHT_STDIN,
        Gate::Manifest,
    )?);
    audit.prompt = Some(create_new_artifact(
        &audit.runtime.sealed.join("prompt.txt"),
        &audit.runtime.sealed_directory,
        PREFLIGHT_PROMPT,
        Gate::Manifest,
    )?);
    audit.schema = Some(create_new_artifact(
        &audit.runtime.sealed.join("schema.json"),
        &audit.runtime.sealed_directory,
        PREFLIGHT_SCHEMA,
        Gate::Manifest,
    )?);
    binary.revalidate()?;
    let command_line = format!("\"{}\" --version", binary.path.display());
    let mut child = ChildTransaction::create(
        &binary.path,
        OsStr::new(&command_line),
        &audit.runtime,
        &audit.environment,
        policy,
        console_creation_mode,
        deadline,
    )?;
    audit.child_created = true;
    audit.child_process_policy_readback = true;
    audit.child_process_policy_flags = Some(child.child_process_policy_flags);
    if let Err(error) = child.prepare_job_membership(policy) {
        audit.descendants_zero = child.drained;
        audit.max_observed_total_processes = Some(child.max_observed_total_processes);
        audit.max_observed_active_processes = Some(child.max_observed_active_processes);
        return Err(error);
    }
    audit.pre_resume_total_processes = Some(child.pre_resume_total_processes);
    audit.pre_resume_active_processes = Some(child.pre_resume_active_processes);
    if let Err(error) = binary.revalidate() {
        return match child.terminate_and_drain() {
            Ok(_) => {
                audit.descendants_zero = child.drained;
                audit.max_observed_total_processes = Some(child.max_observed_total_processes);
                audit.max_observed_active_processes = Some(child.max_observed_active_processes);
                Err(error)
            }
            Err(cleanup_error) => Err(cleanup_error),
        };
    }
    let outcome = match child.run() {
        Ok(value) => value,
        Err(error) => {
            audit.child_resumed = child.resumed;
            audit.descendants_zero = child.drained;
            audit.max_observed_total_processes = Some(child.max_observed_total_processes);
            audit.max_observed_active_processes = Some(child.max_observed_active_processes);
            return Err(error);
        }
    };
    audit.child_resumed = child.resumed;
    audit.descendants_zero = child.drained;
    audit.exit_code = Some(outcome.exit_code);
    audit.timed_out = outcome.timed_out;
    audit.stdout_total = outcome.stdout.total_bytes;
    audit.stderr_total = outcome.stderr.total_bytes;
    audit.stdout_overflow = outcome.stdout.overflow;
    audit.stderr_overflow = outcome.stderr.overflow;
    audit.total_processes = Some(outcome.total_processes);
    audit.active_processes = Some(outcome.active_processes);
    audit.max_observed_total_processes = Some(outcome.max_observed_total_processes);
    audit.max_observed_active_processes = Some(outcome.max_observed_active_processes);
    audit.stdout = Some(create_new_artifact(
        &audit.runtime.output.join("stdout.raw"),
        &audit.runtime.output_directory,
        &outcome.stdout.bytes,
        Gate::OutputAndDeadline,
    )?);
    audit.stderr = Some(create_new_artifact(
        &audit.runtime.output.join("stderr.raw"),
        &audit.runtime.output_directory,
        &outcome.stderr.bytes,
        Gate::OutputAndDeadline,
    )?);
    binary.revalidate()?;
    drop(child);
    Ok(outcome)
}

fn validate_outcome(outcome: &ChildOutcome) -> Result<(), Blocker> {
    if outcome.timed_out {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "PREFLIGHT_DEADLINE_EXCEEDED",
        ));
    }
    if outcome.stdout.overflow || outcome.stderr.overflow {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "PREFLIGHT_OUTPUT_LIMIT_EXCEEDED",
        ));
    }
    if outcome.exit_code == STATUS_DLL_INIT_FAILED
        && outcome.total_processes == 1
        && outcome.active_processes == 0
        && outcome.stdout.total_bytes == 0
        && outcome.stderr.total_bytes == 0
        && !outcome.stdout.overflow
        && !outcome.stderr.overflow
    {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "CODEX_0147_DLL_INIT_FAILED_UNDER_PREFLIGHT_POLICY",
        ));
    }
    if outcome.exit_code != 0 {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "PREFLIGHT_EXIT_NONZERO",
        ));
    }
    if outcome.total_processes != 1 || outcome.active_processes != 0 {
        return Err(Blocker::new(
            Gate::JobDrained,
            "PREFLIGHT_PROCESS_TREE_NOT_EXACT",
        ));
    }
    let stdout = std::str::from_utf8(&outcome.stdout.bytes)
        .map_err(|_| Blocker::new(Gate::OutputAndDeadline, "PREFLIGHT_STDOUT_NOT_UTF8"))?;
    let stderr = std::str::from_utf8(&outcome.stderr.bytes)
        .map_err(|_| Blocker::new(Gate::OutputAndDeadline, "PREFLIGHT_STDERR_NOT_UTF8"))?;
    if stdout != format!("{EXPECTED_CODEX_VERSION}\n") {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "PREFLIGHT_VERSION_OUTPUT_MISMATCH",
        ));
    }
    if !stderr.is_empty() {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "PREFLIGHT_STDERR_NOT_EMPTY",
        ));
    }
    Ok(())
}

fn result_without_receipt(blocker: Blocker) -> NativePreflightResult {
    NativePreflightResult {
        exit_code: 3,
        terminal: "BLOCKED_PREFLIGHT",
        blocker: Some(blocker.code),
        run_root: None,
        receipt_path: None,
        receipt_sha256: None,
        child_created: false,
        child_resumed: false,
        descendants_zero: false,
    }
}

fn result_for_runtime_without_receipt(
    runtime: RuntimeAuthority,
    blocker: Blocker,
) -> NativePreflightResult {
    NativePreflightResult {
        exit_code: 3,
        terminal: "BLOCKED_PREFLIGHT",
        blocker: Some(blocker.code),
        run_root: Some(runtime.run_root.clone()),
        receipt_path: None,
        receipt_sha256: None,
        child_created: false,
        child_resumed: false,
        descendants_zero: false,
    }
}

fn receipt_bytes(
    audit: &PreflightAudit,
    terminal: &'static str,
    blocker: Option<&'static str>,
) -> Vec<u8> {
    let environment_json = audit
        .environment
        .iter()
        .map(|(key, value)| format!("\"{}\":\"{}\"", json_escape(key), json_escape(value)))
        .collect::<Vec<_>>()
        .join(",");
    let blocker_json = blocker
        .map(|value| format!("\"{}\"", json_escape(value)))
        .unwrap_or_else(|| "null".to_owned());
    let binary = identity_json(audit.binary.as_ref());
    let stdin = identity_json(audit.stdin.as_ref().map(|value| &value.identity));
    let prompt = identity_json(audit.prompt.as_ref().map(|value| &value.identity));
    let schema = identity_json(audit.schema.as_ref().map(|value| &value.identity));
    let stdout = identity_json(audit.stdout.as_ref().map(|value| &value.identity));
    let stderr = identity_json(audit.stderr.as_ref().map(|value| &value.identity));
    format!(
        "{{\"schema\":\"pkm.codex.preflight.receipt.v1\",\"terminal\":\"{}\",\"blocker\":{},\"mode\":\"PREFLIGHT_ONLY\",\"console_creation_mode\":\"{}\",\"live\":false,\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"auth_isolation\":false,\"network_enforcement\":false,\"network_usage\":\"UNOBSERVED\",\"credential_access\":\"UNOBSERVED\",\"ambient_environment_inherited\":false,\"run_id\":\"{}\",\"run_root\":\"{}\",\"binary\":{},\"argv\":[\"--version\"],\"environment\":{{{}}},\"stdin\":{},\"prompt\":{},\"schema_artifact\":{},\"startup\":{{\"create_suspended\":true,\"job_list\":true,\"child_process_restricted\":true,\"child_process_policy_readback\":{},\"child_process_policy_flags\":{},\"handle_list\":true,\"kill_on_job_close\":true,\"breakaway\":false,\"pre_resume_job_total_processes\":{},\"pre_resume_job_active_processes\":{}}},\"child_created\":{},\"child_resumed\":{},\"timed_out\":{},\"exit_code\":{},\"stdout_total_bytes\":{},\"stderr_total_bytes\":{},\"stdout_overflow\":{},\"stderr_overflow\":{},\"stdout\":{},\"stderr\":{},\"job_total_processes\":{},\"job_active_processes\":{},\"descendants_zero\":{},\"expected_stdout\":\"{}\\n\",\"expected_stderr\":\"\"}}\n",
        terminal,
        blocker_json,
        audit.console_creation_mode.label(),
        audit.runtime.run_id,
        json_escape(&audit.runtime.run_root.to_string_lossy()),
        binary,
        environment_json,
        stdin,
        prompt,
        schema,
        json_bool(audit.child_process_policy_readback),
        json_u32_or_null(audit.child_process_policy_flags),
        json_u32_or_null(audit.pre_resume_total_processes),
        json_u32_or_null(audit.pre_resume_active_processes),
        json_bool(audit.child_created),
        json_bool(audit.child_resumed),
        json_bool(audit.timed_out),
        json_u32_or_null(audit.exit_code),
        audit.stdout_total,
        audit.stderr_total,
        json_bool(audit.stdout_overflow),
        json_bool(audit.stderr_overflow),
        stdout,
        stderr,
        json_u32_or_null(audit.total_processes),
        json_u32_or_null(audit.active_processes),
        json_bool(audit.descendants_zero),
        EXPECTED_CODEX_VERSION,
    )
    .into_bytes()
}

fn identity_json(identity: Option<&NtfsFileIdentity>) -> String {
    let Some(identity) = identity else {
        return "null".to_owned();
    };
    format!(
        "{{\"path\":\"{}\",\"volume_serial\":{},\"file_id\":\"{}\",\"parent_file_id\":\"{}\",\"length\":{},\"sha256\":\"{}\",\"link_count\":{},\"creation_authority\":\"{}\"}}",
        json_escape(&identity.path.to_string_lossy()),
        identity.volume_serial,
        hex_bytes(&identity.file_id),
        hex_bytes(&identity.parent_file_id),
        identity.length,
        identity.sha256,
        identity.link_count,
        match identity.creation_authority {
            FileCreationAuthority::OfficialReleaseCopy => "OFFICIAL_RELEASE_COPY",
            FileCreationAuthority::BrokerCreateNew => "BROKER_CREATE_NEW",
            FileCreationAuthority::NativeCreateNewOutput => "NATIVE_CREATE_NEW_OUTPUT",
        },
    )
}

fn json_path_or_null(path: Option<&Path>) -> String {
    path.map(|value| format!("\"{}\"", json_escape(&value.to_string_lossy())))
        .unwrap_or_else(|| "null".to_owned())
}

fn json_string_or_null(value: Option<&str>) -> String {
    value
        .map(|value| format!("\"{}\"", json_escape(value)))
        .unwrap_or_else(|| "null".to_owned())
}

fn json_u32_or_null(value: Option<u32>) -> String {
    value
        .map(|value| value.to_string())
        .unwrap_or_else(|| "null".to_owned())
}

const fn json_bool(value: bool) -> &'static str {
    if value {
        "true"
    } else {
        "false"
    }
}

const fn json_bool_or_null(value: Option<bool>) -> &'static str {
    match value {
        Some(true) => "true",
        Some(false) => "false",
        None => "null",
    }
}

fn json_escape(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '\"' => escaped.push_str("\\\""),
            '\\' => escaped.push_str("\\\\"),
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            value if value < ' ' => {
                use std::fmt::Write as _;
                let _ = write!(&mut escaped, "\\u{:04x}", value as u32);
            }
            value => escaped.push(value),
        }
    }
    escaped
}

fn path_utf8(path: &Path, gate: Gate) -> Result<String, Blocker> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| Blocker::new(gate, "PREFLIGHT_PATH_NOT_UTF8"))
}

fn hex_bytes(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        let _ = write!(&mut output, "{byte:02x}");
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_escaping_is_not_a_secret_or_path_injection_seam() {
        assert_eq!(json_escape("a\\b\"c\n"), "a\\\\b\\\"c\\n");
    }

    #[test]
    fn bounded_capture_drains_but_never_retains_past_limit() {
        let reader = std::io::Cursor::new(b"0123456789".as_slice());
        let capture = read_bounded(reader, 4).expect("bounded capture");
        assert_eq!(capture.bytes, b"0123");
        assert_eq!(capture.total_bytes, 10);
        assert!(capture.overflow);
    }

    #[test]
    fn frozen_dll_init_failure_is_the_specific_child_policy_blocker_only_for_exact_facts() {
        let exact = ChildOutcome {
            exit_code: STATUS_DLL_INIT_FAILED,
            timed_out: false,
            stdout: BoundedCapture {
                bytes: Vec::new(),
                total_bytes: 0,
                overflow: false,
            },
            stderr: BoundedCapture {
                bytes: Vec::new(),
                total_bytes: 0,
                overflow: false,
            },
            total_processes: 1,
            active_processes: 0,
            max_observed_total_processes: 1,
            max_observed_active_processes: 1,
        };
        assert_eq!(
            validate_outcome(&exact),
            Err(Blocker::new(
                Gate::OutputAndDeadline,
                "CODEX_0147_DLL_INIT_FAILED_UNDER_PREFLIGHT_POLICY"
            ))
        );

        let not_exact = ChildOutcome {
            stderr: BoundedCapture {
                bytes: b"different failure".to_vec(),
                total_bytes: 17,
                overflow: false,
            },
            ..exact
        };
        assert_eq!(
            validate_outcome(&not_exact),
            Err(Blocker::new(
                Gate::OutputAndDeadline,
                "PREFLIGHT_EXIT_NONZERO"
            ))
        );
    }

    #[test]
    fn diagnostic_json_never_hides_an_unsafe_in_memory_state() {
        let policy = DIAGNOSTIC_POLICIES[0];
        let mut variant = diagnostic_variant_without_receipt(policy, "TEST_ONLY");
        variant.production = true;
        let variant_json = variant.to_json();
        assert!(variant_json.contains("\"production\":true"));
        assert_eq!(
            validate_internal_diagnostic_variant(&variant),
            Err(Blocker::new(
                Gate::Manifest,
                "DIAGNOSTIC_FAIL_CLOSED_CONTRACT_INVALID"
            ))
        );

        let mut summary = diagnostic_suite_failure("TEST_ONLY", vec![variant]);
        summary.live = true;
        let summary_json = summary.to_json();
        assert!(summary_json.contains("\"live\":true"));
        assert_eq!(
            validate_internal_diagnostic_summary(&summary),
            Err(Blocker::new(
                Gate::Manifest,
                "DIAGNOSTIC_SUMMARY_FAIL_CLOSED_CONTRACT_INVALID"
            ))
        );
    }

    #[test]
    fn lpac_nt_query_only_classifies_exact_platform_statuses_as_unsupported() {
        for status in [
            0xC000_0002u32 as i32,
            0xC000_0003u32 as i32,
            0xC000_00BBu32 as i32,
        ] {
            assert_eq!(
                classify_lpac_nt_query_status(status),
                Err(Blocker::new(
                    Gate::PrelaunchObserved,
                    "LPAC_DIRECT_TOKEN_READBACK_UNSUPPORTED",
                ))
            );
        }
        for status in [0xC000_0008u32 as i32, 0xC000_0022u32 as i32] {
            assert_eq!(
                classify_lpac_nt_query_status(status),
                Err(Blocker::new(
                    Gate::PrelaunchObserved,
                    "LPAC_TOKEN_NT_QUERY_FAILED",
                ))
            );
        }
        assert_eq!(classify_lpac_nt_query_status(0), Ok(()));
    }

    #[test]
    fn partial_lpac_create_requires_termination_and_wait_evidence() {
        assert!(lpac_partial_cleanup_proven(true, false, WAIT_OBJECT_0));
        assert!(lpac_partial_cleanup_proven(false, true, WAIT_OBJECT_0));
        assert!(!lpac_partial_cleanup_proven(false, false, WAIT_OBJECT_0));
        assert!(!lpac_partial_cleanup_proven(true, true, WAIT_TIMEOUT));
        assert!(!lpac_partial_child_observed(false, false, false));
        assert!(lpac_partial_child_observed(false, true, false));
        assert!(lpac_partial_child_observed(false, false, true));
        assert!(lpac_partial_child_observed(false, true, true));
        assert!(lpac_partial_child_observed(true, false, false));
    }

    #[test]
    fn lpac_create_failure_preserves_observed_child_and_drain_state() {
        let unproven = LpacChildCreateFailure::after_child(
            Blocker::new(Gate::JobDrained, "INJECTED_DRAIN_FAILURE"),
            false,
            false,
        );
        assert!(unproven.child_created_suspended);
        assert!(!unproven.descendants_zero);
        assert!(!unproven.lpac_attribute_accepted);
        assert!(!unproven.profile_cleanup_allowed());

        let drained = LpacChildCreateFailure::after_child(
            Blocker::new(Gate::PrelaunchObserved, "INJECTED_READBACK_FAILURE"),
            true,
            true,
        );
        assert!(drained.child_created_suspended);
        assert!(drained.descendants_zero);
        assert!(drained.lpac_attribute_accepted);
        assert!(drained.profile_cleanup_allowed());
    }

    #[test]
    fn token_buffer_lengths_are_class_bounded_before_allocation_or_cast() {
        assert_eq!(validate_token_buffer_length(8, 4, 4096), Ok(()));
        for (bytes, minimum, maximum) in [(0, 4, 4096), (3, 4, 4096), (4097, 4, 4096)] {
            assert_eq!(
                validate_token_buffer_length(bytes, minimum, maximum),
                Err(Blocker::new(
                    Gate::PrelaunchObserved,
                    "LPAC_TOKEN_BUFFER_LENGTH_INVALID",
                ))
            );
        }
    }
}
