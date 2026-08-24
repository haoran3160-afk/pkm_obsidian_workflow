use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::{AsRawHandle, FromRawHandle};
use std::path::PathBuf;
use std::time::Duration;

use windows_sys::core::GUID;
use windows_sys::Win32::Foundation::{
    CloseHandle, LocalFree, FWP_E_FILTER_NOT_FOUND, FWP_E_SUBLAYER_NOT_FOUND, HANDLE, HWND,
    INVALID_HANDLE_VALUE,
};
use windows_sys::Win32::NetworkManagement::WindowsFilteringPlatform::{
    FwpmEngineClose0, FwpmEngineOpen0, FwpmFilterAdd0, FwpmFilterGetById0, FwpmFreeMemory0,
    FwpmSubLayerAdd0, FwpmSubLayerGetByKey0, FwpmTransactionAbort0, FwpmTransactionBegin0,
    FwpmTransactionCommit0, FWPM_ACTION0, FWPM_ACTION0_0, FWPM_CONDITION_ALE_PACKAGE_ID,
    FWPM_CONDITION_IP_PROTOCOL, FWPM_CONDITION_IP_REMOTE_ADDRESS, FWPM_CONDITION_IP_REMOTE_PORT,
    FWPM_DISPLAY_DATA0, FWPM_FILTER0, FWPM_FILTER0_0, FWPM_FILTER_CONDITION0,
    FWPM_LAYER_ALE_AUTH_CONNECT_V4, FWPM_LAYER_ALE_AUTH_CONNECT_V6, FWPM_SESSION0,
    FWPM_SESSION_FLAG_DYNAMIC, FWPM_SUBLAYER0, FWP_ACTION_BLOCK, FWP_ACTION_PERMIT, FWP_BYTE_BLOB,
    FWP_CONDITION_VALUE0, FWP_CONDITION_VALUE0_0, FWP_MATCH_EQUAL, FWP_SID, FWP_UINT16, FWP_UINT32,
    FWP_UINT8, FWP_VALUE0, FWP_VALUE0_0,
};
use windows_sys::Win32::Security::Authorization::{
    BuildTrusteeWithSidW, GetEffectiveRightsFromAclW, GetNamedSecurityInfoW, SE_FILE_OBJECT,
    TRUSTEE_W,
};
use windows_sys::Win32::Security::Cryptography::{
    BCryptCloseAlgorithmProvider, BCryptCreateHash, BCryptDestroyHash, BCryptFinishHash,
    BCryptGetProperty, BCryptHashData, BCryptOpenAlgorithmProvider, CertCloseStore,
    CertFindCertificateInStore, CertFreeCertificateContext, CertGetCertificateContextProperty,
    CertGetNameStringW, CryptMsgClose, CryptMsgGetParam, CryptQueryObject, BCRYPT_ALG_HANDLE,
    BCRYPT_HASH_HANDLE, BCRYPT_OBJECT_LENGTH, BCRYPT_SHA256_ALGORITHM, CERT_FIND_SUBJECT_CERT,
    CERT_INFO, CERT_NAME_SIMPLE_DISPLAY_TYPE, CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
    CERT_QUERY_FORMAT_FLAG_BINARY, CERT_QUERY_OBJECT_BLOB, CERT_SHA1_HASH_PROP_ID,
    CMSG_SIGNER_INFO, CMSG_SIGNER_INFO_PARAM, CRYPT_INTEGER_BLOB, HCERTSTORE, PKCS_7_ASN_ENCODING,
    X509_ASN_ENCODING,
};
use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeleteAppContainerProfile, DeriveAppContainerSidFromAppContainerName,
};
use windows_sys::Win32::Security::WinTrust::{
    WinVerifyTrust, WINTRUST_ACTION_GENERIC_VERIFY_V2, WINTRUST_DATA, WINTRUST_DATA_0,
    WINTRUST_FILE_INFO, WTD_CACHE_ONLY_URL_RETRIEVAL, WTD_CHOICE_FILE, WTD_DISABLE_MD2_MD4,
    WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT, WTD_REVOKE_NONE, WTD_STATEACTION_CLOSE,
    WTD_STATEACTION_VERIFY, WTD_UICONTEXT_EXECUTE, WTD_UI_NONE,
};
use windows_sys::Win32::Security::{
    EqualSid, FreeSid, GetLengthSid, GetTokenInformation, IsValidSid, TokenElevation, ACL,
    DACL_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID, TOKEN_ELEVATION, TOKEN_QUERY,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, FileIdInfo, GetFileInformationByHandle, GetFileInformationByHandleEx,
    BY_HANDLE_FILE_INFORMATION, DELETE, FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_GENERIC_EXECUTE, FILE_GENERIC_READ, FILE_GENERIC_WRITE,
    FILE_ID_INFO, FILE_READ_ATTRIBUTES, FILE_SHARE_READ, OPEN_EXISTING,
};
use windows_sys::Win32::System::JobObjects::{
    CreateJobObjectW, JobObjectExtendedLimitInformation, QueryInformationJobObject,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
    JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

use crate::{
    Blocker, Gate, IsolationBackend, JobDrainProof, NtfsFileIdentity, PostCleanupProof,
    PrelaunchObservation, ProcessOutcome, QualificationPlan, EXPECTED_CODEX_EXE_SHA256,
    EXPECTED_CODEX_VERSION,
};

mod preflight;
pub use preflight::{
    run_native_appcontainer_creator_kill_reconciliation_diagnostic,
    run_native_appcontainer_owner_restart_recovery_diagnostic,
    run_native_appcontainer_profile_diagnostic, run_native_child_policy_diagnostic,
    run_native_helper_diagnostic, run_native_lpac_token_diagnostic, run_native_preflight,
    run_native_preflight_diagnostic, run_native_wfp_policy_diagnostic,
    NativeAppContainerCreatorKillReconciliationDiagnosticResult,
    NativeAppContainerOwnerRestartRecoveryDiagnosticResult,
    NativeAppContainerProfileDiagnosticResult, NativeChildPolicyDiagnosticResult,
    NativeHelperDebugEvent, NativeHelperDiagnosticResult, NativeHelperDiagnosticVariantResult,
    NativeHelperHomeChangeEvent, NativeHelperHomeEntry, NativeHelperJobEvent,
    NativeHelperProcessEvidence, NativeLpacTokenDiagnosticResult, NativePreflightDiagnosticResult,
    NativePreflightDiagnosticVariantResult, NativePreflightResult, NativeWfpPolicyDiagnosticResult,
};

const PREFLIGHT_BINARY_PATH: &str = r"D:\personal\obsidian_workflow-runtime\durable\codex\0.147.0-prequal-20260818-01\expanded\codex-x86_64-pc-windows-msvc.exe";
const PREFLIGHT_BINARY_LENGTH: u64 = 298_668_336;
const PREFLIGHT_DEADLINE: Duration = Duration::from_secs(30);

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativePreflightContract {
    pub binary_path: PathBuf,
    pub binary_length: u64,
    pub binary_sha256: &'static str,
    pub argv: [&'static str; 1],
    pub expected_stdout: String,
    pub expected_stderr: &'static str,
    pub deadline_seconds: u64,
    pub mode: &'static str,
    pub console_creation_mode: &'static str,
    pub live: bool,
    pub auth_materialized: bool,
    pub network_requested: bool,
    pub auth_isolation: bool,
    pub network_enforcement: bool,
    pub fixed_environment_keys: [&'static str; 8],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativePreflightDiagnosticContract {
    pub command: &'static str,
    pub terminal: &'static str,
    pub production: bool,
    pub live: bool,
    pub auth_materialized: bool,
    pub network_requested: bool,
    pub variants: [(&'static str, bool, bool, bool); 4],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeHelperDiagnosticContract {
    pub command: &'static str,
    pub terminal: &'static str,
    pub production: bool,
    pub live: bool,
    pub auth_materialized: bool,
    pub network_requested: bool,
    pub variants: [(&'static str, bool, bool, bool, &'static str); 4],
    pub debug_process: bool,
    pub job_completion_port: bool,
    pub codex_home_change_trace: bool,
    pub final_codex_home_inventory: bool,
    pub external_file_access_trace: &'static str,
}

pub fn native_preflight_contract() -> NativePreflightContract {
    NativePreflightContract {
        binary_path: PathBuf::from(PREFLIGHT_BINARY_PATH),
        binary_length: PREFLIGHT_BINARY_LENGTH,
        binary_sha256: EXPECTED_CODEX_EXE_SHA256,
        argv: ["--version"],
        expected_stdout: format!("{EXPECTED_CODEX_VERSION}\n"),
        expected_stderr: "",
        deadline_seconds: PREFLIGHT_DEADLINE.as_secs(),
        mode: "PREFLIGHT_ONLY",
        console_creation_mode: "DETACHED_PROCESS",
        live: false,
        auth_materialized: false,
        network_requested: false,
        auth_isolation: false,
        network_enforcement: false,
        fixed_environment_keys: [
            "ALL_PROXY",
            "CODEX_HOME",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
        ],
    }
}

pub fn native_preflight_diagnostic_contract() -> NativePreflightDiagnosticContract {
    NativePreflightDiagnosticContract {
        command: "diagnose-preflight",
        terminal: "BLOCKED_DIAGNOSTIC",
        production: false,
        live: false,
        auth_materialized: false,
        network_requested: false,
        // (variant, atomic JOB_LIST, CHILD_PROCESS_RESTRICTED,
        // suspended AssignProcessToJobObject before resume)
        variants: [
            ("A", true, true, false),
            ("B", true, false, false),
            ("C", false, true, true),
            ("D", false, false, true),
        ],
    }
}

pub fn native_helper_diagnostic_contract() -> NativeHelperDiagnosticContract {
    NativeHelperDiagnosticContract {
        command: "diagnose-helper",
        terminal: "BLOCKED_HELPER_DIAGNOSTIC",
        production: false,
        live: false,
        auth_materialized: false,
        network_requested: false,
        // (variant, atomic JOB_LIST, CHILD_PROCESS_RESTRICTED, suspended
        // AssignProcessToJobObject before resume, console creation mode).
        // E differs from B only by console mode; F differs from A only by
        // console mode.
        variants: [
            ("B", true, false, false, "CREATE_NO_WINDOW"),
            ("D", false, false, true, "CREATE_NO_WINDOW"),
            ("E", true, false, false, "DETACHED_PROCESS"),
            ("F", true, true, false, "DETACHED_PROCESS"),
        ],
        debug_process: true,
        job_completion_port: true,
        codex_home_change_trace: true,
        final_codex_home_inventory: true,
        external_file_access_trace: "ETW_REQUIRED_NOT_RUN",
    }
}

pub fn native_static_blockers() -> Vec<Blocker> {
    vec![
        Blocker::new(Gate::Manifest, "CLOSED_NATIVE_PLAN_BUILDER_NOT_READY"),
        Blocker::new(
            Gate::Manifest,
            "RUNTIME_ROOT_CREATE_NEW_AUTHORITY_NOT_READY",
        ),
        Blocker::new(Gate::Manifest, "WINDOWS_BUILD_QUALIFICATION_NOT_BOUND"),
        Blocker::new(
            Gate::BinaryIdentity,
            "CODEX_0147_BINARY_QUALIFICATION_NOT_RUN",
        ),
        Blocker::new(
            Gate::BinaryIdentity,
            "CODEX_0147_DETACHED_PREFLIGHT_NOT_RELEASE_BOUND",
        ),
        Blocker::new(
            Gate::PrelaunchObserved,
            "CHILD_PROCESS_RESTRICTION_NEGATIVE_CANARY_NOT_RELEASE_BOUND",
        ),
        Blocker::new(
            Gate::FileBoundary,
            "PREFLIGHT_FULL_TOKEN_AUTH_ISOLATION_NOT_QUALIFIED",
        ),
        Blocker::new(
            Gate::WfpTransaction,
            "PREFLIGHT_NETWORK_ENFORCEMENT_NOT_QUALIFIED",
        ),
        Blocker::new(
            Gate::AppContainerLpac,
            "APPCONTAINER_PROFILE_DIAGNOSTIC_NOT_RELEASE_BOUND",
        ),
        Blocker::new(
            Gate::AppContainerLpac,
            "APPCONTAINER_PROFILE_CRASH_RECOVERY_NOT_QUALIFIED",
        ),
        Blocker::new(
            Gate::AppContainerLpac,
            "LPAC_TOKEN_DIAGNOSTIC_NOT_RELEASE_BOUND",
        ),
        Blocker::new(
            Gate::AppContainerLpac,
            "ZERO_NETWORK_CAPABILITY_LOOPBACK_SMOKE_NOT_QUALIFIED",
        ),
        Blocker::new(
            Gate::ProxyAttestation,
            "PROXY_PROCESS_IDENTITY_NOT_QUALIFIED",
        ),
        Blocker::new(Gate::ProxyAttestation, "PROXY_EXECUTION_NOT_IMPLEMENTED"),
        Blocker::new(
            Gate::ProxyAttestation,
            "PROXY_SECRET_ONESHOT_CHILD_ENV_NOT_READY",
        ),
        Blocker::new(
            Gate::ProxyAttestation,
            "CODEX_0147_PROXY_AUTH_TRACE_REQUIRED",
        ),
        Blocker::new(
            Gate::ProxyAttestation,
            "SUBSCRIPTION_AUDIENCE_TRACE_REQUIRED",
        ),
        Blocker::new(
            Gate::WfpTransaction,
            "WFP_ELEVATED_READBACK_AND_NEGATIVE_MATRIX_REQUIRED",
        ),
        Blocker::new(Gate::LoopbackExemption, "LOOPBACK_EXEMPTION_NOT_QUALIFIED"),
        Blocker::new(Gate::FileBoundary, "ANCESTOR_HANDLE_CHAIN_NOT_QUALIFIED"),
        Blocker::new(Gate::FileBoundary, "AUTH_CACHE_ACL_HANDLE_NOT_QUALIFIED"),
        Blocker::new(Gate::FileBoundary, "FILE_ACCESS_ETW_TRACE_REQUIRED"),
        Blocker::new(
            Gate::JobPrepared,
            "MAINTENANCE_LOCK_LIFECYCLE_NOT_QUALIFIED",
        ),
        Blocker::new(
            Gate::ChildCreatedSuspended,
            "CREATE_SUSPENDED_LPAC_LAUNCH_NOT_READY",
        ),
        Blocker::new(
            Gate::ChildCreatedSuspended,
            "CREATE_RESUME_RAII_TRANSACTION_NOT_READY",
        ),
        Blocker::new(
            Gate::OutputAndDeadline,
            "STDIN_PIPE_BOUNDED_CAPTURE_NOT_READY",
        ),
        Blocker::new(
            Gate::ChildCreatedSuspended,
            "CHILD_ENVIRONMENT_TRACE_NOT_QUALIFIED",
        ),
        Blocker::new(
            Gate::OutputAndDeadline,
            "TRACE_ZERO_TOOL_SUBPROCESS_NOT_INTEGRATED",
        ),
        Blocker::new(
            Gate::OutputAndDeadline,
            "OUTPUT_ARTIFACT_ATTESTATION_NOT_READY",
        ),
        Blocker::new(Gate::OutputAndDeadline, "OUTPUT_SECRET_LEAK_SCAN_NOT_READY"),
        Blocker::new(Gate::OutputAndDeadline, "OS_OBSERVED_CLOSURE_NOT_PRODUCED"),
        Blocker::new(Gate::JobDrained, "JOB_WFP_TEARDOWN_NOT_QUALIFIED"),
        Blocker::new(Gate::CleanupFinalized, "POST_CLEANUP_OBSERVATION_NOT_READY"),
    ]
}

pub fn process_is_elevated() -> Result<bool, Blocker> {
    let mut token = std::ptr::null_mut();
    // SAFETY: GetCurrentProcess is a valid pseudo handle and token points to
    // writable storage for the returned handle.
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
        return Err(Blocker::new(
            Gate::Elevation,
            "TOKEN_ELEVATION_QUERY_FAILED",
        ));
    }
    let mut elevation = TOKEN_ELEVATION { TokenIsElevated: 0 };
    let mut returned = 0u32;
    // SAFETY: token is live, and the typed output buffer and its exact size are
    // supplied for TokenElevation.
    let queried = unsafe {
        GetTokenInformation(
            token,
            TokenElevation,
            (&mut elevation as *mut TOKEN_ELEVATION).cast(),
            std::mem::size_of::<TOKEN_ELEVATION>() as u32,
            &mut returned,
        )
    };
    // SAFETY: token was returned by OpenProcessToken and is closed once.
    let closed = unsafe { CloseHandle(token) };
    if queried == 0 || closed == 0 || returned != std::mem::size_of::<TOKEN_ELEVATION>() as u32 {
        return Err(Blocker::new(
            Gate::Elevation,
            "TOKEN_ELEVATION_QUERY_FAILED",
        ));
    }
    Ok(elevation.TokenIsElevated != 0)
}

struct OwnedCngAlgorithm(BCRYPT_ALG_HANDLE);

impl Drop for OwnedCngAlgorithm {
    fn drop(&mut self) {
        // SAFETY: this handle is owned and closed at most once.
        let _ = unsafe { BCryptCloseAlgorithmProvider(self.0, 0) };
    }
}

struct OwnedCngHash(BCRYPT_HASH_HANDLE);

impl Drop for OwnedCngHash {
    fn drop(&mut self) {
        // SAFETY: this handle is owned and destroyed at most once.
        let _ = unsafe { BCryptDestroyHash(self.0) };
    }
}

fn sha256_open_file(file: &File, gate: Gate) -> Result<String, Blocker> {
    let mut file = file
        .try_clone()
        .map_err(|_| Blocker::new(gate, "FILE_HANDLE_CLONE_FAILED"))?;
    file.seek(SeekFrom::Start(0))
        .map_err(|_| Blocker::new(gate, "FILE_HANDLE_SEEK_FAILED"))?;
    let mut algorithm = std::ptr::null_mut();
    // SAFETY: output storage and static algorithm identifier are valid.
    if unsafe {
        BCryptOpenAlgorithmProvider(&mut algorithm, BCRYPT_SHA256_ALGORITHM, std::ptr::null(), 0)
    } < 0
        || algorithm.is_null()
    {
        return Err(Blocker::new(Gate::BinaryIdentity, "CNG_SHA256_UNAVAILABLE"));
    }
    let algorithm = OwnedCngAlgorithm(algorithm);
    let mut object_length = 0u32;
    let mut returned = 0u32;
    // SAFETY: output points to a u32-sized buffer as requested by this CNG
    // property and the algorithm handle is live.
    if unsafe {
        BCryptGetProperty(
            algorithm.0,
            BCRYPT_OBJECT_LENGTH,
            (&mut object_length as *mut u32).cast(),
            std::mem::size_of::<u32>() as u32,
            &mut returned,
            0,
        )
    } < 0
        || returned != std::mem::size_of::<u32>() as u32
        || object_length == 0
    {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "CNG_HASH_OBJECT_QUERY_FAILED",
        ));
    }
    let mut object = vec![0u8; object_length as usize];
    let mut hash = std::ptr::null_mut();
    // SAFETY: all buffers and lengths are exact and live for this call.
    if unsafe {
        BCryptCreateHash(
            algorithm.0,
            &mut hash,
            object.as_mut_ptr(),
            object_length,
            std::ptr::null(),
            0,
            0,
        )
    } < 0
        || hash.is_null()
    {
        return Err(Blocker::new(Gate::BinaryIdentity, "CNG_HASH_CREATE_FAILED"));
    }
    let hash = OwnedCngHash(hash);
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| Blocker::new(gate, "FILE_HANDLE_READ_FAILED"))?;
        if count == 0 {
            break;
        }
        // SAFETY: the hash handle and the initialized prefix are live.
        if unsafe { BCryptHashData(hash.0, buffer.as_ptr(), count as u32, 0) } < 0 {
            return Err(Blocker::new(Gate::BinaryIdentity, "CNG_HASH_UPDATE_FAILED"));
        }
    }
    let mut digest = [0u8; 32];
    // SAFETY: output is a 32-byte SHA-256 buffer and hash is live.
    if unsafe { BCryptFinishHash(hash.0, digest.as_mut_ptr(), digest.len() as u32, 0) } < 0 {
        return Err(Blocker::new(Gate::BinaryIdentity, "CNG_HASH_FINISH_FAILED"));
    }
    let mut value = String::with_capacity(71);
    value.push_str("sha256:");
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut value, "{byte:02x}")
            .map_err(|_| Blocker::new(Gate::BinaryIdentity, "HASH_FORMAT_FAILED"))?;
    }
    Ok(value)
}

struct VerifiedFile {
    file: File,
    _parent: File,
    expected: NtfsFileIdentity,
}

impl std::fmt::Debug for VerifiedFile {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("VerifiedFile")
            .field("sha256", &self.expected.sha256)
            .field("length", &self.expected.length)
            .finish_non_exhaustive()
    }
}

fn file_id_from_handle(file: &File, gate: Gate) -> Result<(u64, [u8; 16], u32, u64), Blocker> {
    // SAFETY: zero is a valid output baseline.
    let mut id: FILE_ID_INFO = unsafe { std::mem::zeroed() };
    let handle = file.as_raw_handle() as HANDLE;
    // SAFETY: handle is live and the output structure has its exact size.
    if unsafe {
        GetFileInformationByHandleEx(
            handle,
            FileIdInfo,
            (&mut id as *mut FILE_ID_INFO).cast(),
            std::mem::size_of::<FILE_ID_INFO>() as u32,
        )
    } == 0
    {
        return Err(Blocker::new(gate, "FILE_ID_QUERY_FAILED"));
    }
    // SAFETY: zero is a valid output baseline.
    let mut basic: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    // SAFETY: handle and output pointer are valid.
    if unsafe { GetFileInformationByHandle(handle, &mut basic) } == 0 {
        return Err(Blocker::new(gate, "FILE_LINK_COUNT_QUERY_FAILED"));
    }
    let length = ((basic.nFileSizeHigh as u64) << 32) | basic.nFileSizeLow as u64;
    Ok((
        id.VolumeSerialNumber,
        id.FileId.Identifier,
        basic.nNumberOfLinks,
        length,
    ))
}

fn open_verified_file(expected: &NtfsFileIdentity, gate: Gate) -> Result<VerifiedFile, Blocker> {
    reject_reparse_points(&expected.path, gate)?;
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&expected.path)
        .map_err(|_| Blocker::new(gate, "IDENTITY_FILE_OPEN_FAILED"))?;
    let (volume, file_id, links, length) = file_id_from_handle(&file, gate)?;
    let parent = expected
        .path
        .parent()
        .ok_or_else(|| Blocker::new(gate, "IDENTITY_PARENT_MISSING"))?;
    let parent_wide = wide_null(parent.as_os_str());
    // SAFETY: the path is NUL-terminated. The returned handle is converted
    // exactly once into an owned File below. Omitting FILE_SHARE_WRITE and
    // FILE_SHARE_DELETE pins the directory entry while qualification runs.
    let parent_handle = unsafe {
        CreateFileW(
            parent_wide.as_ptr(),
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if parent_handle == INVALID_HANDLE_VALUE {
        return Err(Blocker::new(gate, "IDENTITY_PARENT_OPEN_FAILED"));
    }
    // SAFETY: parent_handle is a unique, valid CreateFileW result and its
    // ownership is transferred to File.
    let parent_file = unsafe { File::from_raw_handle(parent_handle.cast()) };
    let (parent_volume, parent_id, _, _) = file_id_from_handle(&parent_file, gate)?;
    if volume != expected.volume_serial
        || parent_volume != expected.volume_serial
        || file_id != expected.file_id
        || parent_id != expected.parent_file_id
        || links != 1
        || links != expected.link_count
        || length != expected.length
        || sha256_open_file(&file, gate)? != expected.sha256
    {
        return Err(Blocker::new(gate, "NTFS_IDENTITY_MISMATCH"));
    }
    Ok(VerifiedFile {
        file,
        _parent: parent_file,
        expected: expected.clone(),
    })
}

fn verify_path_still_same(expected: &NtfsFileIdentity, gate: Gate) -> Result<(), Blocker> {
    let _ = open_verified_file(expected, gate)?;
    Ok(())
}

fn parse_pe_identity(image: &[u8]) -> Result<(), Blocker> {
    if image.get(0..2) != Some(b"MZ") || image.len() < 0x40 {
        return Err(Blocker::new(Gate::BinaryIdentity, "PE_DOS_HEADER_INVALID"));
    }
    let offset = u32::from_le_bytes(
        image[0x3c..0x40]
            .try_into()
            .map_err(|_| Blocker::new(Gate::BinaryIdentity, "PE_OFFSET_INVALID"))?,
    ) as usize;
    let header_end = offset
        .checked_add(26)
        .ok_or_else(|| Blocker::new(Gate::BinaryIdentity, "PE_OFFSET_INVALID"))?;
    if header_end > image.len() || image.get(offset..offset + 4) != Some(b"PE\0\0") {
        return Err(Blocker::new(Gate::BinaryIdentity, "PE_HEADER_INVALID"));
    }
    let machine = u16::from_le_bytes([image[offset + 4], image[offset + 5]]);
    if machine != 0x8664 {
        return Err(Blocker::new(Gate::BinaryIdentity, "PE_MACHINE_NOT_AMD64"));
    }
    let optional_magic = u16::from_le_bytes([image[offset + 24], image[offset + 25]]);
    if optional_magic != 0x20b {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "PE_OPTIONAL_HEADER_NOT_PE32_PLUS",
        ));
    }
    Ok(())
}

fn verify_pe_file(file: &File) -> Result<(), Blocker> {
    let mut file = file
        .try_clone()
        .map_err(|_| Blocker::new(Gate::BinaryIdentity, "BINARY_HANDLE_CLONE_FAILED"))?;
    file.seek(SeekFrom::Start(0))
        .map_err(|_| Blocker::new(Gate::BinaryIdentity, "BINARY_HANDLE_SEEK_FAILED"))?;
    let mut image = Vec::with_capacity(64 * 1024);
    std::io::Read::by_ref(&mut file)
        .take(1024 * 1024)
        .read_to_end(&mut image)
        .map_err(|_| Blocker::new(Gate::BinaryIdentity, "BINARY_READ_FAILED"))?;
    parse_pe_identity(&image)
}

fn verify_authenticode(path: &std::path::Path, file: &File) -> Result<(), Blocker> {
    let wide = wide_null(path.as_os_str());
    // SAFETY: zero is the documented initialization baseline for these C
    // structs; every required field is then set explicitly.
    let mut file_info: WINTRUST_FILE_INFO = unsafe { std::mem::zeroed() };
    file_info.cbStruct = std::mem::size_of::<WINTRUST_FILE_INFO>() as u32;
    file_info.pcwszFilePath = wide.as_ptr();
    file_info.hFile = file.as_raw_handle() as HANDLE;
    // SAFETY: see above; union arm matches WTD_CHOICE_FILE.
    let mut data: WINTRUST_DATA = unsafe { std::mem::zeroed() };
    data.cbStruct = std::mem::size_of::<WINTRUST_DATA>() as u32;
    data.dwUIChoice = WTD_UI_NONE;
    data.fdwRevocationChecks = WTD_REVOKE_NONE;
    data.dwUnionChoice = WTD_CHOICE_FILE;
    data.Anonymous = WINTRUST_DATA_0 {
        pFile: &mut file_info,
    };
    data.dwStateAction = WTD_STATEACTION_VERIFY;
    data.dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL
        | WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT
        | WTD_DISABLE_MD2_MD4;
    data.dwUIContext = WTD_UICONTEXT_EXECUTE;
    let mut action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    // SAFETY: data and action point to initialized structures for this
    // synchronous verification call; no UI or network retrieval is allowed.
    let status = unsafe {
        WinVerifyTrust(
            std::ptr::null_mut::<core::ffi::c_void>() as HWND,
            &mut action,
            (&mut data as *mut WINTRUST_DATA).cast(),
        )
    };
    data.dwStateAction = WTD_STATEACTION_CLOSE;
    // SAFETY: closes any state created by the immediately preceding call.
    let _ = unsafe {
        WinVerifyTrust(
            std::ptr::null_mut::<core::ffi::c_void>() as HWND,
            &mut action,
            (&mut data as *mut WINTRUST_DATA).cast(),
        )
    };
    if status != 0 {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "AUTHENTICODE_OFFLINE_VERIFY_FAILED",
        ));
    }
    verify_authenticode_signer(file)
}

struct OwnedCertStore(HCERTSTORE);

impl Drop for OwnedCertStore {
    fn drop(&mut self) {
        // SAFETY: store was returned by CryptQueryObject and is closed once.
        let _ = unsafe { CertCloseStore(self.0, 0) };
    }
}

struct OwnedCryptMsg(*mut core::ffi::c_void);

impl Drop for OwnedCryptMsg {
    fn drop(&mut self) {
        // SAFETY: message was returned by CryptQueryObject and is closed once.
        let _ = unsafe { CryptMsgClose(self.0) };
    }
}

fn verify_authenticode_signer(file: &File) -> Result<(), Blocker> {
    const EXPECTED_SIGNER: &str = "OpenAI OpCo, LLC";
    const EXPECTED_THUMBPRINT: [u8; 20] = [
        0x8b, 0x0a, 0xdf, 0xb8, 0x40, 0xe1, 0x41, 0xda, 0xd3, 0x04, 0x4d, 0x2b, 0x5a, 0xc8, 0x19,
        0x87, 0x3d, 0xde, 0x35, 0x90,
    ];
    let mut file = file
        .try_clone()
        .map_err(|_| Blocker::new(Gate::BinaryIdentity, "BINARY_HANDLE_CLONE_FAILED"))?;
    file.seek(SeekFrom::Start(0))
        .map_err(|_| Blocker::new(Gate::BinaryIdentity, "BINARY_HANDLE_SEEK_FAILED"))?;
    let length = file
        .metadata()
        .map_err(|_| Blocker::new(Gate::BinaryIdentity, "BINARY_METADATA_FAILED"))?
        .len();
    if length == 0 || length > u32::MAX as u64 {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "BINARY_SIGNER_BLOB_SIZE_INVALID",
        ));
    }
    let mut image = Vec::with_capacity(length as usize);
    file.read_to_end(&mut image)
        .map_err(|_| Blocker::new(Gate::BinaryIdentity, "BINARY_READ_FAILED"))?;
    if image.len() as u64 != length {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "BINARY_SIGNER_BLOB_LENGTH_MISMATCH",
        ));
    }
    let blob = CRYPT_INTEGER_BLOB {
        cbData: image.len() as u32,
        pbData: image.as_mut_ptr(),
    };
    let mut encoding = 0u32;
    let mut content = 0u32;
    let mut format = 0u32;
    let mut store = std::ptr::null_mut();
    let mut message = std::ptr::null_mut();
    // SAFETY: blob points to the exact bytes read from the already-pinned
    // executable handle and every output pointer is writable. Store/message
    // ownership is adopted immediately below.
    if unsafe {
        CryptQueryObject(
            CERT_QUERY_OBJECT_BLOB,
            (&blob as *const CRYPT_INTEGER_BLOB).cast(),
            CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
            CERT_QUERY_FORMAT_FLAG_BINARY,
            0,
            &mut encoding,
            &mut content,
            &mut format,
            &mut store,
            &mut message,
            std::ptr::null_mut(),
        )
    } == 0
        || store.is_null()
        || message.is_null()
    {
        if !store.is_null() {
            // SAFETY: partial-success store still follows close contract.
            let _ = unsafe { CertCloseStore(store, 0) };
        }
        if !message.is_null() {
            // SAFETY: partial-success message still follows close contract.
            let _ = unsafe { CryptMsgClose(message) };
        }
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "AUTHENTICODE_SIGNER_QUERY_FAILED",
        ));
    }
    let store = OwnedCertStore(store);
    let message = OwnedCryptMsg(message);
    let mut signer_size = 0u32;
    // SAFETY: size-only query on a live message.
    if unsafe {
        CryptMsgGetParam(
            message.0,
            CMSG_SIGNER_INFO_PARAM,
            0,
            std::ptr::null_mut(),
            &mut signer_size,
        )
    } == 0
        || signer_size < std::mem::size_of::<CMSG_SIGNER_INFO>() as u32
    {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "AUTHENTICODE_SIGNER_INFO_SIZE_FAILED",
        ));
    }
    let words = (signer_size as usize).div_ceil(std::mem::size_of::<usize>());
    let mut signer_storage = vec![0usize; words];
    // SAFETY: aligned storage has the exact byte capacity reported above.
    if unsafe {
        CryptMsgGetParam(
            message.0,
            CMSG_SIGNER_INFO_PARAM,
            0,
            signer_storage.as_mut_ptr().cast(),
            &mut signer_size,
        )
    } == 0
    {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "AUTHENTICODE_SIGNER_INFO_FAILED",
        ));
    }
    // SAFETY: CryptMsgGetParam populated an aligned CMSG_SIGNER_INFO at the
    // start of signer_storage.
    let signer = unsafe { &*(signer_storage.as_ptr().cast::<CMSG_SIGNER_INFO>()) };
    // SAFETY: zero is the baseline; only issuer and serial are consumed by
    // CERT_FIND_SUBJECT_CERT.
    let mut certificate_info: CERT_INFO = unsafe { std::mem::zeroed() };
    certificate_info.Issuer = signer.Issuer;
    certificate_info.SerialNumber = signer.SerialNumber;
    // SAFETY: store and certificate_info are live; no previous context.
    let certificate = unsafe {
        CertFindCertificateInStore(
            store.0,
            X509_ASN_ENCODING | PKCS_7_ASN_ENCODING,
            0,
            CERT_FIND_SUBJECT_CERT,
            (&certificate_info as *const CERT_INFO).cast(),
            std::ptr::null(),
        )
    };
    if certificate.is_null() {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "AUTHENTICODE_SIGNER_CERT_NOT_FOUND",
        ));
    }
    // SAFETY: first call queries required UTF-16 length.
    let name_length = unsafe {
        CertGetNameStringW(
            certificate,
            CERT_NAME_SIMPLE_DISPLAY_TYPE,
            0,
            std::ptr::null(),
            std::ptr::null_mut(),
            0,
        )
    };
    let mut name = vec![0u16; name_length as usize];
    // SAFETY: buffer has exactly the queried capacity and certificate is live.
    let copied = if name_length > 1 {
        unsafe {
            CertGetNameStringW(
                certificate,
                CERT_NAME_SIMPLE_DISPLAY_TYPE,
                0,
                std::ptr::null(),
                name.as_mut_ptr(),
                name_length,
            )
        }
    } else {
        0
    };
    let mut thumbprint = [0u8; 20];
    let mut thumbprint_size = thumbprint.len() as u32;
    // SAFETY: certificate is live and output has the exact SHA-1 property size.
    let thumbprint_ok = unsafe {
        CertGetCertificateContextProperty(
            certificate,
            CERT_SHA1_HASH_PROP_ID,
            thumbprint.as_mut_ptr().cast(),
            &mut thumbprint_size,
        )
    };
    // SAFETY: certificate context was returned by CertFind* and is freed once.
    let freed = unsafe { CertFreeCertificateContext(certificate) };
    if copied != name_length
        || thumbprint_ok == 0
        || thumbprint_size != thumbprint.len() as u32
        || thumbprint != EXPECTED_THUMBPRINT
        || freed == 0
    {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "AUTHENTICODE_SIGNER_NAME_FAILED",
        ));
    }
    let actual = String::from_utf16(&name[..name.len().saturating_sub(1)])
        .map_err(|_| Blocker::new(Gate::BinaryIdentity, "AUTHENTICODE_SIGNER_NAME_INVALID"))?;
    if actual != EXPECTED_SIGNER {
        return Err(Blocker::new(
            Gate::BinaryIdentity,
            "AUTHENTICODE_SIGNER_MISMATCH",
        ));
    }
    Ok(())
}

fn wide_null(value: &std::ffi::OsStr) -> Vec<u16> {
    value.encode_wide().chain(std::iter::once(0)).collect()
}

struct OwnedSid(PSID);

impl Drop for OwnedSid {
    fn drop(&mut self) {
        // SAFETY: AppContainer APIs return a SID released with FreeSid. This
        // owner is never cloned and frees it exactly once.
        let _ = unsafe { FreeSid(self.0) };
    }
}

impl std::fmt::Debug for OwnedSid {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("OwnedSid(<redacted>)")
    }
}

fn per_run_profile_name(plan: &QualificationPlan) -> Result<String, Blocker> {
    let run_id = plan
        .runtime_root
        .file_name()
        .and_then(std::ffi::OsStr::to_str)
        .and_then(|value| value.strip_prefix("codex-t13b-"))
        .ok_or_else(|| Blocker::new(Gate::AppContainerLpac, "APPCONTAINER_RUN_ID_INVALID"))?;
    if !(16..=64).contains(&run_id.len()) || !run_id.bytes().all(|value| value.is_ascii_hexdigit())
    {
        return Err(Blocker::new(
            Gate::AppContainerLpac,
            "APPCONTAINER_RUN_ID_INVALID",
        ));
    }
    Ok(format!("OpenAI.PKMWorkflow.Codex.T13b.{run_id}"))
}

fn delete_profile(profile_name: &str) -> Result<(), Blocker> {
    let profile_name = wide_null(std::ffi::OsStr::new(profile_name));
    // SAFETY: the name is a live NUL-terminated UTF-16 buffer for the call.
    if unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) } < 0 {
        return Err(Blocker::new(
            Gate::AppContainerRemoved,
            "APPCONTAINER_PROFILE_DELETE_FAILED",
        ));
    }
    Ok(())
}

fn delete_profile_if_present(profile_name: &str) -> Result<bool, Blocker> {
    let profile_name = wide_null(std::ffi::OsStr::new(profile_name));
    // SAFETY: the name is a live NUL-terminated UTF-16 buffer for the call.
    let status = unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) };
    classify_profile_reconcile_delete_status(status)
}

fn classify_profile_reconcile_delete_status(status: i32) -> Result<bool, Blocker> {
    if status == 0 {
        return Ok(true);
    }
    const HRESULT_FILE_NOT_FOUND: i32 = 0x8007_0002u32 as i32;
    const HRESULT_NOT_FOUND: i32 = 0x8007_0490u32 as i32;
    if status == HRESULT_FILE_NOT_FOUND || status == HRESULT_NOT_FOUND {
        return Ok(false);
    }
    Err(Blocker::new(
        Gate::AppContainerRemoved,
        "APPCONTAINER_PROFILE_RECONCILE_DELETE_FAILED",
    ))
}

fn create_zero_capability_profile(profile_name: &str) -> Result<OwnedSid, Blocker> {
    let profile_name_wide = wide_null(std::ffi::OsStr::new(profile_name));
    let display_name = wide_null(std::ffi::OsStr::new("PKM Codex T1.3b qualification"));
    let description = wide_null(std::ffi::OsStr::new(
        "Shadow-only LPAC profile; not a production activation",
    ));
    let mut sid = std::ptr::null_mut();
    // SAFETY: strings are NUL-terminated. Capability count is deliberately
    // zero; loopback exemption plus exact WFP policy must be qualified before
    // any broader network capability could be considered.
    let create_status = unsafe {
        CreateAppContainerProfile(
            profile_name_wide.as_ptr(),
            display_name.as_ptr(),
            description.as_ptr(),
            std::ptr::null(),
            0,
            &mut sid,
        )
    };
    const HRESULT_ALREADY_EXISTS: i32 = 0x8007_00b7u32 as i32;
    if create_status == HRESULT_ALREADY_EXISTS {
        return Err(Blocker::new(
            Gate::AppContainerLpac,
            "APPCONTAINER_PROFILE_ALREADY_EXISTS",
        ));
    } else if create_status < 0 {
        return Err(Blocker::new(
            Gate::AppContainerLpac,
            "APPCONTAINER_PROFILE_CREATE_FAILED",
        ));
    }
    if sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
        if !sid.is_null() {
            // SAFETY: failed validation still returns FreeSid-owned storage.
            let _ = unsafe { FreeSid(sid) };
        }
        return match delete_profile(profile_name) {
            Ok(()) => Err(Blocker::new(
                Gate::AppContainerLpac,
                "APPCONTAINER_SID_INVALID",
            )),
            Err(_) => Err(Blocker::new(
                Gate::AppContainerRemoved,
                "APPCONTAINER_PROFILE_ROLLBACK_FAILED",
            )),
        };
    }
    Ok(OwnedSid(sid))
}

fn derive_appcontainer_sid(profile_name: &str) -> Result<OwnedSid, Blocker> {
    let profile_name = wide_null(std::ffi::OsStr::new(profile_name));
    let mut sid = std::ptr::null_mut();
    // SAFETY: profile_name is a live NUL-terminated buffer and sid is writable.
    if unsafe { DeriveAppContainerSidFromAppContainerName(profile_name.as_ptr(), &mut sid) } < 0
        || sid.is_null()
        || unsafe { IsValidSid(sid) } == 0
    {
        if !sid.is_null() {
            // SAFETY: any returned SID follows the FreeSid ownership contract.
            let _ = unsafe { FreeSid(sid) };
        }
        return Err(Blocker::new(
            Gate::AppContainerLpac,
            "APPCONTAINER_SID_DERIVE_FAILED",
        ));
    }
    Ok(OwnedSid(sid))
}

fn appcontainer_sids_equal(left: &OwnedSid, right: &OwnedSid) -> bool {
    // SAFETY: both values own live, validated SIDs.
    unsafe { EqualSid(left.0, right.0) != 0 }
}

fn appcontainer_sid_bytes(sid: &OwnedSid) -> Result<Vec<u8>, Blocker> {
    // SAFETY: sid owns a live, validated SID.
    let length = unsafe { GetLengthSid(sid.0) };
    if length == 0 || length > 4_096 {
        return Err(Blocker::new(
            Gate::AppContainerLpac,
            "APPCONTAINER_SID_LENGTH_INVALID",
        ));
    }
    // SAFETY: GetLengthSid returned the exact readable SID byte length.
    Ok(unsafe { std::slice::from_raw_parts(sid.0.cast::<u8>(), length as usize) }.to_vec())
}

const WFP_SUBLAYER_KEY: GUID = GUID::from_u128(0xf41baeb8_1706_4cab_91ad_6f7bb3941c70);

struct WfpEngine(HANDLE);

impl WfpEngine {
    fn close(&mut self) -> Result<(), Blocker> {
        self.close_with(|handle| {
            // SAFETY: the dynamic engine handle remains owned by self for the
            // duration of the synchronous close call.
            unsafe { FwpmEngineClose0(handle) }
        })
    }

    fn close_with<F>(&mut self, close: F) -> Result<(), Blocker>
    where
        F: FnOnce(HANDLE) -> u32,
    {
        if self.0.is_null() {
            return Ok(());
        }
        // Ownership is cleared only after the close authority confirms
        // success. Failure retains the exact handle for an explicit retry.
        if close(self.0) != 0 {
            return Err(Blocker::new(
                Gate::WfpPolicyRemoved,
                "WFP_ENGINE_CLOSE_FAILED",
            ));
        }
        self.0 = std::ptr::null_mut();
        Ok(())
    }
}

impl Drop for WfpEngine {
    fn drop(&mut self) {
        // Best-effort process teardown remains fail-closed because an open
        // dynamic session only preserves the restrictive filters. Qualified
        // cleanup uses the checked close path before this destructor.
        let _ = self.close();
    }
}

struct WfpPolicyResource {
    engine: Option<WfpEngine>,
    filter_ids: [u64; 3],
    cleanup_verified: bool,
}

impl WfpPolicyResource {
    fn close_and_verify_with<C, F>(&mut self, close: C, verify: F) -> Result<(), Blocker>
    where
        C: FnOnce(&mut Option<WfpEngine>) -> Result<(), Blocker>,
        F: FnOnce([u64; 3]) -> Result<(), Blocker>,
    {
        if self.cleanup_verified {
            return Err(Blocker::new(
                Gate::WfpPolicyRemoved,
                "WFP_CLEANUP_ALREADY_VERIFIED",
            ));
        }
        // Closing the dynamic session is the removal operation. A close error
        // retains the owned handle and must not start absence verification.
        // After a confirmed close, retain the exact filter IDs until an
        // independent engine has proved both filters and the sublayer absent.
        close(&mut self.engine)?;
        verify(self.filter_ids)?;
        self.cleanup_verified = true;
        Ok(())
    }

    fn close_and_verify(&mut self) -> Result<(), Blocker> {
        self.close_and_verify_with(
            |engine_slot| {
                if let Some(engine) = engine_slot.as_mut() {
                    engine.close()?;
                }
                engine_slot.take();
                Ok(())
            },
            verify_wfp_dynamic_cleanup,
        )
    }
}

impl std::fmt::Debug for WfpPolicyResource {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("WfpPolicyResource")
            .field("engine_active", &self.engine.is_some())
            .field("filter_count", &self.filter_ids.len())
            .field("cleanup_verified", &self.cleanup_verified)
            .finish()
    }
}

struct OwnedKernelHandle(HANDLE);

impl OwnedKernelHandle {
    fn into_raw(mut self) -> HANDLE {
        let handle = self.0;
        self.0 = std::ptr::null_mut();
        handle
    }
}

impl Drop for OwnedKernelHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: owned kernel handle is closed exactly once.
            let _ = unsafe { CloseHandle(self.0) };
        }
    }
}

impl std::fmt::Debug for OwnedKernelHandle {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("OwnedKernelHandle(<redacted>)")
    }
}

fn create_qualified_job() -> Result<OwnedKernelHandle, Blocker> {
    // SAFETY: null security/name creates an unnamed job.
    let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if handle.is_null() {
        return Err(Blocker::new(Gate::JobPrepared, "JOB_CREATE_FAILED"));
    }
    let handle = OwnedKernelHandle(handle);
    // SAFETY: zero is the documented baseline.
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    limits.BasicLimitInformation.LimitFlags =
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    limits.BasicLimitInformation.ActiveProcessLimit = 1;
    // SAFETY: the typed structure is live with its exact size.
    if unsafe {
        SetInformationJobObject(
            handle.0,
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
    } == 0
    {
        return Err(Blocker::new(Gate::JobPrepared, "JOB_LIMITS_SET_FAILED"));
    }
    // SAFETY: zero is the output baseline and the buffer has exact size.
    let mut actual: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    let mut returned = 0u32;
    if unsafe {
        QueryInformationJobObject(
            handle.0,
            JobObjectExtendedLimitInformation,
            (&mut actual as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            &mut returned,
        )
    } == 0
        || returned != std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32
    {
        return Err(Blocker::new(Gate::JobPrepared, "JOB_LIMITS_QUERY_FAILED"));
    }
    let flags = actual.BasicLimitInformation.LimitFlags;
    if flags & (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
        != (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
        || flags & (JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK) != 0
        || actual.BasicLimitInformation.ActiveProcessLimit != 1
    {
        return Err(Blocker::new(Gate::JobPrepared, "JOB_LIMITS_MISMATCH"));
    }
    Ok(handle)
}

impl std::fmt::Debug for WfpEngine {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("WfpEngine(<dynamic>)")
    }
}

struct WfpTransaction<'a> {
    engine: &'a WfpEngine,
    active: bool,
}

impl<'a> WfpTransaction<'a> {
    fn begin(engine: &'a WfpEngine) -> Result<Self, Blocker> {
        // SAFETY: live engine and documented zero transaction flags.
        if unsafe { FwpmTransactionBegin0(engine.0, 0) } != 0 {
            return Err(Blocker::new(
                Gate::WfpTransaction,
                "WFP_TRANSACTION_BEGIN_FAILED",
            ));
        }
        Ok(Self {
            engine,
            active: true,
        })
    }

    fn commit(mut self) -> Result<(), Blocker> {
        // SAFETY: transaction is active on this live engine.
        let status = unsafe { FwpmTransactionCommit0(self.engine.0) };
        if status == 0 {
            self.active = false;
            Ok(())
        } else {
            Err(Blocker::new(
                Gate::WfpTransaction,
                "WFP_TRANSACTION_COMMIT_FAILED",
            ))
        }
    }
}

impl Drop for WfpTransaction<'_> {
    fn drop(&mut self) {
        if self.active {
            // SAFETY: best-effort abort of the active transaction; engine
            // remains live for this guard's lifetime.
            let _ = unsafe { FwpmTransactionAbort0(self.engine.0) };
        }
    }
}

fn open_dynamic_wfp_engine() -> Result<WfpEngine, Blocker> {
    let mut name = wide_null(std::ffi::OsStr::new("PKM Codex T1.3b dynamic isolation"));
    let mut description = wide_null(std::ffi::OsStr::new(
        "Package-SID default deny; shadow qualification only",
    ));
    // SAFETY: zero is the documented baseline; all relevant session fields are
    // then initialized.
    let mut session: FWPM_SESSION0 = unsafe { std::mem::zeroed() };
    session.displayData = FWPM_DISPLAY_DATA0 {
        name: name.as_mut_ptr(),
        description: description.as_mut_ptr(),
    };
    session.flags = FWPM_SESSION_FLAG_DYNAMIC;
    session.txnWaitTimeoutInMSec = 2_000;
    let mut handle = std::ptr::null_mut();
    // RPC_C_AUTHN_WINNT is 10. Local engine, no explicit credentials.
    // SAFETY: output and session pointers are live for this synchronous call.
    if unsafe {
        FwpmEngineOpen0(
            std::ptr::null(),
            10,
            std::ptr::null(),
            &session,
            &mut handle,
        )
    } != 0
        || handle.is_null()
    {
        return Err(Blocker::new(Gate::WfpTransaction, "WFP_ENGINE_OPEN_FAILED"));
    }
    Ok(WfpEngine(handle))
}

fn add_sublayer(engine: &WfpEngine) -> Result<(), Blocker> {
    let mut name = wide_null(std::ffi::OsStr::new("PKM Codex T1.3b isolation"));
    let mut description = wide_null(std::ffi::OsStr::new(
        "Dynamic package-SID scoped allow-one/default-deny",
    ));
    let sublayer = FWPM_SUBLAYER0 {
        subLayerKey: WFP_SUBLAYER_KEY,
        displayData: FWPM_DISPLAY_DATA0 {
            name: name.as_mut_ptr(),
            description: description.as_mut_ptr(),
        },
        flags: 0,
        providerKey: std::ptr::null_mut(),
        providerData: FWP_BYTE_BLOB {
            size: 0,
            data: std::ptr::null_mut(),
        },
        weight: u16::MAX,
    };
    // SAFETY: all pointers are valid for the synchronous add call; no custom
    // security descriptor is supplied.
    if unsafe { FwpmSubLayerAdd0(engine.0, &sublayer, std::ptr::null_mut()) } != 0 {
        return Err(Blocker::new(
            Gate::WfpTransaction,
            "WFP_SUBLAYER_ADD_FAILED",
        ));
    }
    Ok(())
}

fn sid_condition(sid: PSID) -> FWPM_FILTER_CONDITION0 {
    FWPM_FILTER_CONDITION0 {
        fieldKey: FWPM_CONDITION_ALE_PACKAGE_ID,
        matchType: FWP_MATCH_EQUAL,
        conditionValue: FWP_CONDITION_VALUE0 {
            r#type: FWP_SID,
            Anonymous: FWP_CONDITION_VALUE0_0 { sid: sid.cast() },
        },
    }
}

fn uint32_condition(field: GUID, value: u32) -> FWPM_FILTER_CONDITION0 {
    FWPM_FILTER_CONDITION0 {
        fieldKey: field,
        matchType: FWP_MATCH_EQUAL,
        conditionValue: FWP_CONDITION_VALUE0 {
            r#type: FWP_UINT32,
            Anonymous: FWP_CONDITION_VALUE0_0 { uint32: value },
        },
    }
}

fn uint16_condition(field: GUID, value: u16) -> FWPM_FILTER_CONDITION0 {
    FWPM_FILTER_CONDITION0 {
        fieldKey: field,
        matchType: FWP_MATCH_EQUAL,
        conditionValue: FWP_CONDITION_VALUE0 {
            r#type: FWP_UINT16,
            Anonymous: FWP_CONDITION_VALUE0_0 { uint16: value },
        },
    }
}

fn uint8_condition(field: GUID, value: u8) -> FWPM_FILTER_CONDITION0 {
    FWPM_FILTER_CONDITION0 {
        fieldKey: field,
        matchType: FWP_MATCH_EQUAL,
        conditionValue: FWP_CONDITION_VALUE0 {
            r#type: FWP_UINT8,
            Anonymous: FWP_CONDITION_VALUE0_0 { uint8: value },
        },
    }
}

fn add_filter(
    engine: &WfpEngine,
    layer: GUID,
    action_type: u32,
    weight: u8,
    conditions: &mut [FWPM_FILTER_CONDITION0],
) -> Result<u64, Blocker> {
    let mut name = wide_null(std::ffi::OsStr::new("PKM Codex T1.3b package policy"));
    let mut description = wide_null(std::ffi::OsStr::new(
        "Dynamic; removed automatically with qualification session",
    ));
    // A terminating filter uses no callout/filter-type union value.
    let action = FWPM_ACTION0 {
        r#type: action_type,
        // SAFETY: all-zero GUID is the documented unused union value for
        // terminating permit/block actions.
        Anonymous: FWPM_ACTION0_0 {
            filterType: GUID::from_u128(0),
        },
    };
    let filter = FWPM_FILTER0 {
        filterKey: GUID::from_u128(0),
        displayData: FWPM_DISPLAY_DATA0 {
            name: name.as_mut_ptr(),
            description: description.as_mut_ptr(),
        },
        flags: 0,
        providerKey: std::ptr::null_mut(),
        providerData: FWP_BYTE_BLOB {
            size: 0,
            data: std::ptr::null_mut(),
        },
        layerKey: layer,
        subLayerKey: WFP_SUBLAYER_KEY,
        weight: FWP_VALUE0 {
            r#type: FWP_UINT8,
            Anonymous: FWP_VALUE0_0 { uint8: weight },
        },
        numFilterConditions: conditions.len() as u32,
        filterCondition: conditions.as_mut_ptr(),
        action,
        Anonymous: FWPM_FILTER0_0 { rawContext: 0 },
        reserved: std::ptr::null_mut(),
        filterId: 0,
        effectiveWeight: FWP_VALUE0 {
            r#type: FWP_UINT8,
            Anonymous: FWP_VALUE0_0 { uint8: 0 },
        },
    };
    let mut id = 0u64;
    // SAFETY: filter and its conditions stay live for this synchronous call.
    if unsafe { FwpmFilterAdd0(engine.0, &filter, std::ptr::null_mut(), &mut id) } != 0 || id == 0 {
        return Err(Blocker::new(Gate::WfpTransaction, "WFP_FILTER_ADD_FAILED"));
    }
    Ok(id)
}

#[derive(Clone, Copy)]
enum ExpectedWfpValue {
    Sid(PSID),
    U32(u32),
    U16(u16),
    U8(u8),
}

#[derive(Clone, Copy)]
struct ExpectedWfpCondition {
    field: GUID,
    value: ExpectedWfpValue,
}

fn wfp_condition_matches(actual: &FWPM_FILTER_CONDITION0, expected: &ExpectedWfpCondition) -> bool {
    if !guid_eq(actual.fieldKey, expected.field) || actual.matchType != FWP_MATCH_EQUAL {
        return false;
    }
    match expected.value {
        ExpectedWfpValue::Sid(expected_sid) => {
            if actual.conditionValue.r#type != FWP_SID || expected_sid.is_null() {
                return false;
            }
            // SAFETY: the active union member is selected by FWP_SID and BFE
            // owns the returned SID for the lifetime of the filter allocation.
            let actual_sid = unsafe { actual.conditionValue.Anonymous.sid };
            !actual_sid.is_null() && unsafe { EqualSid(actual_sid.cast(), expected_sid) != 0 }
        }
        ExpectedWfpValue::U32(expected_value) => {
            actual.conditionValue.r#type == FWP_UINT32
                // SAFETY: active union member is selected by FWP_UINT32.
                && unsafe { actual.conditionValue.Anonymous.uint32 } == expected_value
        }
        ExpectedWfpValue::U16(expected_value) => {
            actual.conditionValue.r#type == FWP_UINT16
                // SAFETY: active union member is selected by FWP_UINT16.
                && unsafe { actual.conditionValue.Anonymous.uint16 } == expected_value
        }
        ExpectedWfpValue::U8(expected_value) => {
            actual.conditionValue.r#type == FWP_UINT8
                // SAFETY: active union member is selected by FWP_UINT8.
                && unsafe { actual.conditionValue.Anonymous.uint8 } == expected_value
        }
    }
}

fn verify_filter(
    engine: &WfpEngine,
    id: u64,
    expected_layer: GUID,
    expected_action: u32,
    expected_weight: u8,
    expected_conditions: &[ExpectedWfpCondition],
) -> Result<(), Blocker> {
    let mut filter = std::ptr::null_mut();
    // SAFETY: output pointer is writable and engine/id are live.
    if unsafe { FwpmFilterGetById0(engine.0, id, &mut filter) } != 0 || filter.is_null() {
        return Err(Blocker::new(
            Gate::WfpTransaction,
            "WFP_FILTER_REOPEN_FAILED",
        ));
    }
    // SAFETY: BFE returned a valid FWPM_FILTER0 pointer until FwpmFreeMemory0.
    let matches = unsafe {
        let condition_count = (*filter).numFilterConditions as usize;
        let conditions = if condition_count == 0 || (*filter).filterCondition.is_null() {
            &[][..]
        } else {
            std::slice::from_raw_parts((*filter).filterCondition, condition_count)
        };
        (*filter).filterId == id
            && guid_eq((*filter).layerKey, expected_layer)
            && guid_eq((*filter).subLayerKey, WFP_SUBLAYER_KEY)
            && (*filter).flags == 0
            && (*filter).providerKey.is_null()
            && (*filter).action.r#type == expected_action
            && (*filter).weight.r#type == FWP_UINT8
            && (*filter).weight.Anonymous.uint8 == expected_weight
            && condition_count == expected_conditions.len()
            && expected_conditions.iter().all(|expected| {
                conditions
                    .iter()
                    .filter(|actual| wfp_condition_matches(actual, expected))
                    .count()
                    == 1
            })
    };
    let mut allocation = filter.cast::<core::ffi::c_void>();
    // SAFETY: allocation was returned by a Fwpm*Get API and is freed once.
    unsafe { FwpmFreeMemory0(&mut allocation) };
    if !matches {
        return Err(Blocker::new(
            Gate::WfpTransaction,
            "WFP_FILTER_REOPEN_MISMATCH",
        ));
    }
    Ok(())
}

fn guid_eq(left: GUID, right: GUID) -> bool {
    left.data1 == right.data1
        && left.data2 == right.data2
        && left.data3 == right.data3
        && left.data4 == right.data4
}

fn install_wfp_default_deny(sid: PSID, port: u16) -> Result<(WfpEngine, [u64; 3]), Blocker> {
    let engine = open_dynamic_wfp_engine()?;
    let transaction = WfpTransaction::begin(&engine)?;
    add_sublayer(&engine)?;

    let mut allow_v4 = [
        sid_condition(sid),
        uint32_condition(FWPM_CONDITION_IP_REMOTE_ADDRESS, 0x7f00_0001),
        uint16_condition(FWPM_CONDITION_IP_REMOTE_PORT, port),
        uint8_condition(FWPM_CONDITION_IP_PROTOCOL, 6),
    ];
    let allow_id = add_filter(
        &engine,
        FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        FWP_ACTION_PERMIT,
        15,
        &mut allow_v4,
    )?;
    let mut block_v4 = [sid_condition(sid)];
    let block_v4_id = add_filter(
        &engine,
        FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        FWP_ACTION_BLOCK,
        0,
        &mut block_v4,
    )?;
    let mut block_v6 = [sid_condition(sid)];
    let block_v6_id = add_filter(
        &engine,
        FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        FWP_ACTION_BLOCK,
        0,
        &mut block_v6,
    )?;
    transaction.commit()?;

    let expected_allow_v4 = [
        ExpectedWfpCondition {
            field: FWPM_CONDITION_ALE_PACKAGE_ID,
            value: ExpectedWfpValue::Sid(sid),
        },
        ExpectedWfpCondition {
            field: FWPM_CONDITION_IP_REMOTE_ADDRESS,
            value: ExpectedWfpValue::U32(0x7f00_0001),
        },
        ExpectedWfpCondition {
            field: FWPM_CONDITION_IP_REMOTE_PORT,
            value: ExpectedWfpValue::U16(port),
        },
        ExpectedWfpCondition {
            field: FWPM_CONDITION_IP_PROTOCOL,
            value: ExpectedWfpValue::U8(6),
        },
    ];
    let expected_package_only = [ExpectedWfpCondition {
        field: FWPM_CONDITION_ALE_PACKAGE_ID,
        value: ExpectedWfpValue::Sid(sid),
    }];

    verify_filter(
        &engine,
        allow_id,
        FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        FWP_ACTION_PERMIT,
        15,
        &expected_allow_v4,
    )?;
    verify_filter(
        &engine,
        block_v4_id,
        FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        FWP_ACTION_BLOCK,
        0,
        &expected_package_only,
    )?;
    verify_filter(
        &engine,
        block_v6_id,
        FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        FWP_ACTION_BLOCK,
        0,
        &expected_package_only,
    )?;
    Ok((engine, [allow_id, block_v4_id, block_v6_id]))
}

fn verify_wfp_dynamic_cleanup(filter_ids: [u64; 3]) -> Result<(), Blocker> {
    let engine = open_dynamic_wfp_engine()?;
    for id in filter_ids {
        let mut filter = std::ptr::null_mut();
        // SAFETY: output pointer is writable and engine/id are live.
        let status = unsafe { FwpmFilterGetById0(engine.0, id, &mut filter) };
        if status == 0 {
            let mut allocation = filter.cast::<core::ffi::c_void>();
            if !filter.is_null() {
                // SAFETY: successful Get returned a BFE-owned allocation.
                unsafe { FwpmFreeMemory0(&mut allocation) };
            }
            return Err(Blocker::new(
                Gate::WfpPolicyRemoved,
                "WFP_DYNAMIC_FILTER_STILL_PRESENT",
            ));
        }
        if status != FWP_E_FILTER_NOT_FOUND as u32 || !filter.is_null() {
            return Err(Blocker::new(
                Gate::WfpPolicyRemoved,
                "WFP_DYNAMIC_FILTER_CLEANUP_QUERY_FAILED",
            ));
        }
    }
    let mut sublayer = std::ptr::null_mut();
    // SAFETY: output pointer is writable and engine/key are live.
    let status = unsafe { FwpmSubLayerGetByKey0(engine.0, &WFP_SUBLAYER_KEY, &mut sublayer) };
    if status == 0 {
        let mut allocation = sublayer.cast::<core::ffi::c_void>();
        if !sublayer.is_null() {
            // SAFETY: successful Get returned a BFE-owned allocation.
            unsafe { FwpmFreeMemory0(&mut allocation) };
        }
        return Err(Blocker::new(
            Gate::WfpPolicyRemoved,
            "WFP_DYNAMIC_SUBLAYER_STILL_PRESENT",
        ));
    }
    if status != FWP_E_SUBLAYER_NOT_FOUND as u32 || !sublayer.is_null() {
        return Err(Blocker::new(
            Gate::WfpPolicyRemoved,
            "WFP_DYNAMIC_SUBLAYER_CLEANUP_QUERY_FAILED",
        ));
    }
    Ok(())
}

fn reject_reparse_points(path: &std::path::Path, gate: Gate) -> Result<(), Blocker> {
    for ancestor in path.ancestors() {
        if ancestor.as_os_str().is_empty() {
            continue;
        }
        let metadata = std::fs::symlink_metadata(ancestor)
            .map_err(|_| Blocker::new(gate, "BOUNDARY_PATH_METADATA_FAILED"))?;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(Blocker::new(gate, "BOUNDARY_REPARSE_POINT_FORBIDDEN"));
        }
    }
    Ok(())
}

fn effective_rights(path: &std::path::Path, sid: PSID) -> Result<u32, Blocker> {
    let path = wide_null(path.as_os_str());
    let mut dacl: *mut ACL = std::ptr::null_mut();
    let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
    // SAFETY: path is NUL-terminated and all requested output pointers are
    // writable. The returned descriptor is LocalFree-owned.
    let status = unsafe {
        GetNamedSecurityInfoW(
            path.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut dacl,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 || dacl.is_null() || descriptor.is_null() {
        if !descriptor.is_null() {
            // SAFETY: descriptor is returned by GetNamedSecurityInfoW.
            let _ = unsafe { LocalFree(descriptor) };
        }
        return Err(Blocker::new(
            Gate::FileBoundary,
            "BOUNDARY_DACL_QUERY_FAILED",
        ));
    }
    // SAFETY: zero is the documented baseline and BuildTrusteeWithSidW fills
    // the structure with a non-owning SID reference.
    let mut trustee: TRUSTEE_W = unsafe { std::mem::zeroed() };
    unsafe { BuildTrusteeWithSidW(&mut trustee, sid) };
    let mut rights = 0u32;
    // SAFETY: dacl, trustee and rights remain valid for this synchronous call.
    let rights_status = unsafe { GetEffectiveRightsFromAclW(dacl, &trustee, &mut rights) };
    // SAFETY: descriptor is LocalFree-owned and released exactly once.
    let freed = unsafe { LocalFree(descriptor) };
    if rights_status != 0 || !freed.is_null() {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "BOUNDARY_EFFECTIVE_RIGHTS_FAILED",
        ));
    }
    Ok(rights)
}

fn require_rights(
    path: &std::path::Path,
    sid: PSID,
    required: u32,
    forbidden: u32,
) -> Result<(), Blocker> {
    let rights = effective_rights(path, sid)?;
    if rights & required != required || rights & forbidden != 0 {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "BOUNDARY_DACL_RIGHTS_MISMATCH",
        ));
    }
    Ok(())
}

fn verify_boundary_paths(plan: &QualificationPlan, sid: PSID) -> Result<(), Blocker> {
    let canonical_root = plan
        .runtime_root
        .canonicalize()
        .map_err(|_| Blocker::new(Gate::FileBoundary, "RUNTIME_ROOT_CANONICALIZE_FAILED"))?;
    let temp = plan.run_root.join("temp");
    let directories = [&plan.runtime_root, &plan.codex_home, &plan.run_root, &temp];
    for directory in directories {
        if !directory.is_dir() {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "BOUNDARY_DIRECTORY_MISSING",
            ));
        }
        reject_reparse_points(directory, Gate::FileBoundary)?;
        let canonical = directory
            .canonicalize()
            .map_err(|_| Blocker::new(Gate::FileBoundary, "BOUNDARY_CANONICALIZE_FAILED"))?;
        if !canonical.starts_with(&canonical_root) {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "BOUNDARY_CANONICAL_PATH_ESCAPE",
            ));
        }
    }
    let sealed_files = [&plan.evidence_path, &plan.schema_path, &plan.prompt_path];
    for file in sealed_files {
        if !file.is_file() {
            return Err(Blocker::new(Gate::FileBoundary, "SEALED_INPUT_MISSING"));
        }
        reject_reparse_points(file, Gate::FileBoundary)?;
        let canonical = file
            .canonicalize()
            .map_err(|_| Blocker::new(Gate::FileBoundary, "SEALED_INPUT_CANONICALIZE_FAILED"))?;
        if !canonical.starts_with(&canonical_root) {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "SEALED_INPUT_CANONICAL_PATH_ESCAPE",
            ));
        }
    }
    // The child receives the frozen prompt and evidence payload only through
    // the exact manifest-bound stdin pipe. It may read the output schema, but
    // it must not be able to open the broker's prompt/evidence source files.
    require_rights(
        &plan.schema_path,
        sid,
        FILE_GENERIC_READ,
        FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE | DELETE,
    )?;
    for file in [&plan.evidence_path, &plan.prompt_path] {
        require_rights(
            file,
            sid,
            0,
            FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE | DELETE,
        )?;
    }
    reject_reparse_points(&plan.binary_path, Gate::FileBoundary)?;
    require_rights(
        &plan.binary_path,
        sid,
        FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
        FILE_GENERIC_WRITE | DELETE,
    )?;
    require_rights(
        &plan.runtime_root,
        sid,
        FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
        DELETE,
    )?;
    for directory in [&plan.codex_home, &plan.run_root, &temp] {
        require_rights(
            directory,
            sid,
            FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE,
            0,
        )?;
    }
    Ok(())
}

#[derive(Debug)]
pub struct NativeWindowsBackend {
    child_created: bool,
    child_resumed: bool,
    secret_materialized_in_child: bool,
    // Resource fields are declared in fail-closed drop order. The job handle
    // must close before the dynamic WFP engine so KILL_ON_JOB_CLOSE takes
    // effect while outbound policy is still installed.
    job: Option<OwnedKernelHandle>,
    binary_file: Option<VerifiedFile>,
    sealed_files: Vec<VerifiedFile>,
    wfp_policy: Option<WfpPolicyResource>,
    per_run_package_identity_qualified: bool,
    appcontainer_sid: Option<OwnedSid>,
    appcontainer_profile_name: Option<String>,
    proxy_attested: bool,
    loopback_exemption_installed: bool,
    file_boundary_verified: bool,
}

impl NativeWindowsBackend {
    pub const fn new() -> Self {
        Self {
            child_created: false,
            child_resumed: false,
            secret_materialized_in_child: false,
            job: None,
            binary_file: None,
            sealed_files: Vec::new(),
            wfp_policy: None,
            per_run_package_identity_qualified: false,
            appcontainer_sid: None,
            appcontainer_profile_name: None,
            proxy_attested: false,
            loopback_exemption_installed: false,
            file_boundary_verified: false,
        }
    }

    pub const fn child_created(&self) -> bool {
        self.child_created
    }

    pub const fn child_resumed(&self) -> bool {
        self.child_resumed
    }

    pub const fn secret_materialized_in_child(&self) -> bool {
        self.secret_materialized_in_child
    }
}

impl IsolationBackend for NativeWindowsBackend {
    fn require_elevation(&mut self) -> Result<(), Blocker> {
        match process_is_elevated()? {
            true => Ok(()),
            false => Err(Blocker::new(Gate::Elevation, "ELEVATION_REQUIRED")),
        }
    }

    fn verify_binary(&mut self, plan: &QualificationPlan) -> Result<(), Blocker> {
        if !plan.binary_path.is_file() || self.binary_file.is_some() {
            return Err(Blocker::new(Gate::BinaryIdentity, "BINARY_NOT_FILE"));
        }
        let binary = open_verified_file(&plan.binary_identity, Gate::BinaryIdentity)?;
        verify_pe_file(&binary.file)?;
        verify_authenticode(&plan.binary_path, &binary.file)?;
        verify_path_still_same(&plan.binary_identity, Gate::BinaryIdentity)?;
        self.binary_file = Some(binary);
        Ok(())
    }

    fn prepare_lpac_profile(&mut self, plan: &QualificationPlan) -> Result<(), Blocker> {
        if !self.per_run_package_identity_qualified {
            return Err(Blocker::new(
                Gate::AppContainerLpac,
                "PER_RUN_APPCONTAINER_IDENTITY_NOT_QUALIFIED",
            ));
        }
        if self.appcontainer_sid.is_some() || self.appcontainer_profile_name.is_some() {
            return Err(Blocker::new(
                Gate::AppContainerLpac,
                "APPCONTAINER_PROFILE_REUSE_FORBIDDEN",
            ));
        }
        let profile_name = per_run_profile_name(plan)?;
        let sid = create_zero_capability_profile(&profile_name)?;
        self.appcontainer_sid = Some(sid);
        self.appcontainer_profile_name = Some(profile_name);
        Ok(())
    }

    fn install_loopback_exemption(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        // AppContainer loopback requires an explicit OS network-isolation
        // exemption in addition to WFP. Installing, read-back verification,
        // maintenance-lock serialization, crash recovery, and exact cleanup
        // have not yet been qualified; a persistent broad exemption is not an
        // acceptable substitute.
        Err(Blocker::new(
            Gate::LoopbackExemption,
            "LOOPBACK_EXEMPTION_NOT_QUALIFIED",
        ))
    }

    fn attest_proxy(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        // Readiness is a broker control-channel HMAC proof over the canonical
        // Python ProxyManifest/ProxyReadiness protocol. A TCP echo is neither
        // process identity nor readiness and would consume the one-shot data
        // listener, so no fallback is permitted.
        Err(Blocker::new(
            Gate::ProxyAttestation,
            "PROXY_PROCESS_IDENTITY_NOT_QUALIFIED",
        ))
    }

    fn install_wfp_policy(&mut self, plan: &QualificationPlan) -> Result<(), Blocker> {
        if !self.proxy_attested || self.wfp_policy.is_some() {
            return Err(Blocker::new(
                Gate::WfpTransaction,
                "WFP_TRANSACTION_STATE_INVALID",
            ));
        }
        let sid = self
            .appcontainer_sid
            .as_ref()
            .ok_or_else(|| Blocker::new(Gate::WfpTransaction, "PACKAGE_SID_MISSING"))?;
        let (engine, filter_ids) = install_wfp_default_deny(sid.0, plan.proxy_port)?;
        self.wfp_policy = Some(WfpPolicyResource {
            engine: Some(engine),
            filter_ids,
            cleanup_verified: false,
        });
        Ok(())
    }

    fn verify_file_boundary(&mut self, plan: &QualificationPlan) -> Result<(), Blocker> {
        if !self
            .wfp_policy
            .as_ref()
            .is_some_and(|policy| policy.engine.is_some() && !policy.cleanup_verified)
        {
            return Err(Blocker::new(Gate::FileBoundary, "WFP_POLICY_NOT_ACTIVE"));
        }
        let sid = self
            .appcontainer_sid
            .as_ref()
            .ok_or_else(|| Blocker::new(Gate::FileBoundary, "PACKAGE_SID_MISSING"))?;
        verify_boundary_paths(plan, sid.0)?;
        if !self.sealed_files.is_empty() {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "SEALED_INPUT_HANDLE_REUSE_FORBIDDEN",
            ));
        }
        self.sealed_files = [
            &plan.evidence_identity,
            &plan.schema_identity,
            &plan.prompt_identity,
        ]
        .into_iter()
        .map(|identity| open_verified_file(identity, Gate::FileBoundary))
        .collect::<Result<Vec<_>, _>>()?;
        Err(Blocker::new(
            Gate::FileBoundary,
            "FILE_ACCESS_ETW_TRACE_REQUIRED",
        ))
    }

    fn prepare_job(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        if !self.file_boundary_verified || self.job.is_some() {
            return Err(Blocker::new(
                Gate::JobPrepared,
                "JOB_PRECONDITION_NOT_QUALIFIED",
            ));
        }
        self.job = Some(create_qualified_job()?);
        Ok(())
    }

    fn observe_prelaunch(
        &mut self,
        _plan: &QualificationPlan,
    ) -> Result<PrelaunchObservation, Blocker> {
        Err(Blocker::new(
            Gate::PrelaunchObserved,
            "OS_PRELAUNCH_OBSERVATION_NOT_READY",
        ))
    }

    fn create_suspended(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        Err(Blocker::new(
            Gate::ChildCreatedSuspended,
            "SUSPENDED_LAUNCH_NOT_READY",
        ))
    }

    fn resume(&mut self) -> Result<(), Blocker> {
        Err(Blocker::new(Gate::ChildResumed, "CHILD_NOT_CREATED"))
    }

    fn wait_and_capture(&mut self, _plan: &QualificationPlan) -> Result<ProcessOutcome, Blocker> {
        Err(Blocker::new(Gate::OutputAndDeadline, "CHILD_NOT_RESUMED"))
    }

    fn terminate_job(&mut self) -> Result<(), Blocker> {
        if self.job.is_none() {
            Ok(())
        } else {
            Err(Blocker::new(Gate::JobTerminated, "JOB_OBJECT_NOT_READY"))
        }
    }

    fn drain_job(&mut self) -> Result<JobDrainProof, Blocker> {
        if self.job.is_none() {
            Err(Blocker::new(Gate::JobDrained, "JOB_PROOF_UNAVAILABLE"))
        } else {
            Err(Blocker::new(Gate::JobDrained, "JOB_OBJECT_NOT_READY"))
        }
    }

    fn rollback_file_boundary(&mut self) -> Result<(), Blocker> {
        self.sealed_files.clear();
        self.file_boundary_verified = false;
        Ok(())
    }

    fn remove_loopback_exemption(&mut self) -> Result<(), Blocker> {
        if !self.loopback_exemption_installed {
            Ok(())
        } else {
            Err(Blocker::new(
                Gate::LoopbackExemptionRemoved,
                "LOOPBACK_EXEMPTION_CLEANUP_NOT_READY",
            ))
        }
    }

    fn remove_wfp_policy(&mut self) -> Result<(), Blocker> {
        let Some(policy) = self.wfp_policy.as_mut() else {
            return Ok(());
        };
        policy.close_and_verify()?;
        self.wfp_policy.take();
        Ok(())
    }

    fn stop_proxy(&mut self) -> Result<(), Blocker> {
        if !self.proxy_attested {
            Ok(())
        } else {
            Err(Blocker::new(
                Gate::ProxyStopped,
                "PROXY_PROCESS_CLEANUP_NOT_READY",
            ))
        }
    }

    fn remove_lpac_profile(&mut self) -> Result<(), Blocker> {
        let Some(profile_name) = self.appcontainer_profile_name.as_deref() else {
            return if self.appcontainer_sid.is_none() {
                Ok(())
            } else {
                Err(Blocker::new(
                    Gate::AppContainerRemoved,
                    "APPCONTAINER_PROFILE_STATE_INVALID",
                ))
            };
        };
        delete_profile(profile_name)?;
        self.appcontainer_sid.take();
        self.appcontainer_profile_name.take();
        Ok(())
    }

    fn finalize_cleanup(
        &mut self,
        _plan: &QualificationPlan,
        _drain: Option<&JobDrainProof>,
    ) -> Result<Option<PostCleanupProof>, Blocker> {
        // The native live path has not acquired a maintenance lock yet. Once
        // that path exists, this method must be its last action and must return
        // an OS-read-back proof only after every prior cleanup is durable.
        Ok(None)
    }
}

impl Default for NativeWindowsBackend {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pe_parser_requires_pe32_plus_amd64_executable() {
        let mut image = vec![0u8; 512];
        image[0..2].copy_from_slice(b"MZ");
        image[0x3c..0x40].copy_from_slice(&(0x80u32).to_le_bytes());
        image[0x80..0x84].copy_from_slice(b"PE\0\0");
        image[0x84..0x86].copy_from_slice(&(0x8664u16).to_le_bytes());
        image[0x98..0x9a].copy_from_slice(&(0x20bu16).to_le_bytes());
        assert_eq!(parse_pe_identity(&image), Ok(()));

        image[0x84..0x86].copy_from_slice(&(0x14cu16).to_le_bytes());
        assert_eq!(
            parse_pe_identity(&image),
            Err(Blocker::new(Gate::BinaryIdentity, "PE_MACHINE_NOT_AMD64"))
        );
    }

    #[test]
    fn wfp_condition_match_rejects_field_type_and_value_substitution() {
        let expected = ExpectedWfpCondition {
            field: FWPM_CONDITION_IP_REMOTE_PORT,
            value: ExpectedWfpValue::U16(43_117),
        };
        let exact = uint16_condition(FWPM_CONDITION_IP_REMOTE_PORT, 43_117);
        assert!(wfp_condition_matches(&exact, &expected));

        let wrong_field = uint16_condition(FWPM_CONDITION_IP_PROTOCOL, 43_117);
        assert!(!wfp_condition_matches(&wrong_field, &expected));
        let wrong_type = uint32_condition(FWPM_CONDITION_IP_REMOTE_PORT, 43_117);
        assert!(!wfp_condition_matches(&wrong_type, &expected));
        let wrong_value = uint16_condition(FWPM_CONDITION_IP_REMOTE_PORT, 43_118);
        assert!(!wfp_condition_matches(&wrong_value, &expected));
    }

    #[test]
    fn wfp_resource_retains_ids_until_post_drop_cleanup_is_verified() {
        let filter_ids = [11, 22, 33];
        let mut resource = WfpPolicyResource {
            engine: Some(WfpEngine(std::ptr::null_mut())),
            filter_ids,
            cleanup_verified: false,
        };

        let verify_called = std::cell::Cell::new(false);
        let failed_close = resource.close_and_verify_with(
            |engine| {
                assert!(engine.is_some());
                Err(Blocker::new(
                    Gate::WfpPolicyRemoved,
                    "INJECTED_WFP_ENGINE_CLOSE_FAILURE",
                ))
            },
            |_observed| {
                verify_called.set(true);
                Ok(())
            },
        );
        assert_eq!(
            failed_close,
            Err(Blocker::new(
                Gate::WfpPolicyRemoved,
                "INJECTED_WFP_ENGINE_CLOSE_FAILURE",
            ))
        );
        assert!(!verify_called.get());
        assert!(resource.engine.is_some());
        assert!(!resource.cleanup_verified);
        assert_eq!(resource.filter_ids, filter_ids);

        let failed_readback = resource.close_and_verify_with(
            |engine| {
                assert!(engine.take().is_some());
                Ok(())
            },
            |observed| {
                assert_eq!(observed, filter_ids);
                Err(Blocker::new(
                    Gate::WfpPolicyRemoved,
                    "INJECTED_WFP_CLEANUP_READBACK_FAILURE",
                ))
            },
        );
        assert_eq!(
            failed_readback,
            Err(Blocker::new(
                Gate::WfpPolicyRemoved,
                "INJECTED_WFP_CLEANUP_READBACK_FAILURE",
            ))
        );
        assert!(resource.engine.is_none());
        assert!(!resource.cleanup_verified);
        assert_eq!(resource.filter_ids, filter_ids);

        resource
            .close_and_verify_with(
                |engine| {
                    assert!(engine.is_none());
                    Ok(())
                },
                |observed| {
                    assert_eq!(observed, filter_ids);
                    Ok(())
                },
            )
            .expect("retry exact post-drop cleanup readback");
        assert!(resource.cleanup_verified);
        assert_eq!(resource.filter_ids, filter_ids);
    }

    #[test]
    fn wfp_engine_close_failure_retains_the_owned_handle_for_retry() {
        let fake_handle = 1usize as HANDLE;
        let mut engine = WfpEngine(fake_handle);

        let failed = engine.close_with(|observed| {
            assert_eq!(observed, fake_handle);
            5
        });
        assert_eq!(
            failed,
            Err(Blocker::new(
                Gate::WfpPolicyRemoved,
                "WFP_ENGINE_CLOSE_FAILED",
            ))
        );
        assert_eq!(engine.0, fake_handle);

        engine
            .close_with(|observed| {
                assert_eq!(observed, fake_handle);
                0
            })
            .expect("retry WFP engine close");
        assert!(engine.0.is_null());
    }

    #[test]
    fn profile_reconcile_delete_classifies_only_exact_absence_statuses() {
        assert_eq!(classify_profile_reconcile_delete_status(0), Ok(true));
        for status in [0x8007_0002u32 as i32, 0x8007_0490u32 as i32] {
            assert_eq!(classify_profile_reconcile_delete_status(status), Ok(false));
        }
        for status in [0x8007_0005u32 as i32, 0x8000_4005u32 as i32] {
            assert_eq!(
                classify_profile_reconcile_delete_status(status),
                Err(Blocker::new(
                    Gate::AppContainerRemoved,
                    "APPCONTAINER_PROFILE_RECONCILE_DELETE_FAILED",
                ))
            );
        }
    }
}
