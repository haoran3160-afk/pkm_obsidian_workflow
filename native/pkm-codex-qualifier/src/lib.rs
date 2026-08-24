#![deny(unsafe_op_in_unsafe_fn)]

use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use windows_sys::Win32::Security::Cryptography::{
    BCryptCloseAlgorithmProvider, BCryptHash, BCryptOpenAlgorithmProvider,
    BCRYPT_ALG_HANDLE_HMAC_FLAG, BCRYPT_SHA256_ALGORITHM,
};
mod windows_backend;

pub use windows_backend::{
    native_helper_diagnostic_contract, native_preflight_contract,
    native_preflight_diagnostic_contract, native_static_blockers, process_is_elevated,
    run_native_appcontainer_creator_kill_reconciliation_diagnostic,
    run_native_appcontainer_owner_restart_recovery_diagnostic,
    run_native_appcontainer_profile_diagnostic, run_native_child_policy_diagnostic,
    run_native_helper_diagnostic, run_native_lpac_token_diagnostic, run_native_preflight,
    run_native_preflight_diagnostic, run_native_wfp_policy_diagnostic,
    NativeAppContainerCreatorKillReconciliationDiagnosticResult,
    NativeAppContainerOwnerRestartRecoveryDiagnosticResult,
    NativeAppContainerProfileDiagnosticResult, NativeChildPolicyDiagnosticResult,
    NativeHelperDebugEvent, NativeHelperDiagnosticContract, NativeHelperDiagnosticResult,
    NativeHelperDiagnosticVariantResult, NativeHelperHomeChangeEvent, NativeHelperHomeEntry,
    NativeHelperJobEvent, NativeHelperProcessEvidence, NativeLpacTokenDiagnosticResult,
    NativePreflightContract, NativePreflightDiagnosticContract, NativePreflightDiagnosticResult,
    NativePreflightDiagnosticVariantResult, NativePreflightResult, NativeWfpPolicyDiagnosticResult,
    NativeWindowsBackend,
};

pub const EXPECTED_CODEX_VERSION: &str = "codex-cli 0.147.0";
pub const EXPECTED_CODEX_EXE_SHA256: &str =
    "sha256:935a1911ed2556e4ffcec995f4886ac2ac425863ba26fed264df62e30272ad9d";
pub const EXPECTED_CODEX_ARCHIVE_SHA256: &str =
    "sha256:a1c073ede409178502b1ff11bed635d2b644424352f9d4f3999de8272c6aebbd";
pub const GOLDEN_PROXY_PORT: u16 = 43_117;
pub const MAX_DEADLINE: Duration = Duration::from_secs(15 * 60);
pub const MAX_STDOUT_BYTES: usize = 1_048_576;
pub const MAX_STDERR_BYTES: usize = 262_144;
pub const MAX_STDIN_BYTES: usize = 300_000;

const PROXY_TOKEN_DOMAIN: &[u8] = b"PKM-CODEX-T13B-PROXY-BASIC-TOKEN-V1\0";
const PROXY_READINESS_HMAC_DOMAIN: &[u8] = b"PKM-CODEX-T13B-PROXY-READINESS-HMAC-V1\0";
const PROXY_USERNAME_SHA256: &str =
    "sha256:5f27b95a2a488a37d08dd32e7ea7977a39d791e29aa7911b1a74038f6f076827";

const MANIFEST_DOMAIN: &[u8] = b"PKM-CODEX-T13B-LIVE-QUALIFICATION-MANIFEST-V1\0";
// HTTPS_PROXY is intentionally absent: its secret-bearing value is created
// once in the suspended child's environment block, never stored in a clonable
// plan/map. The plan binds its structured endpoint/user/token hash instead.
const EXPECTED_ENV_KEYS: [&str; 4] = ["CODEX_HOME", "NO_PROXY", "TEMP", "TMP"];

pub const FROZEN_CONFIG_OVERRIDES: &[&str] = &[
    "allow_login_shell=false",
    "analytics.enabled=false",
    "feedback.enabled=false",
    "forced_login_method=\"chatgpt\"",
    "notify=[]",
    "otel.exporter=\"none\"",
    "otel.log_user_prompt=false",
    "otel.metrics_exporter=\"none\"",
    "otel.trace_exporter=\"none\"",
    "project_doc_max_bytes=0",
    "project_root_markers=[]",
    "project_doc_fallback_filenames=[]",
    "features.shell_tool=false",
    "features.unified_exec=false",
    "features.code_mode=false",
    "features.code_mode_only=false",
    "features.code_mode_host=false",
    "features.apply_patch_freeform=false",
    "features.web_search=false",
    "features.web_search_request=false",
    "features.web_search_cached=false",
    "features.standalone_web_search=false",
    "web_search=\"disabled\"",
    "features.apps=false",
    "features.enable_mcp_apps=false",
    "features.connectors=false",
    "features.browser_use=false",
    "features.browser_use_external=false",
    "features.browser_use_full_cdp_access=false",
    "features.in_app_browser=false",
    "features.computer_use=false",
    "features.plugins=false",
    "features.remote_plugin=false",
    "features.recommended_plugins=false",
    "features.plugin_hooks=false",
    "features.plugin_sharing=false",
    "features.memory_tool=false",
    "features.memories=false",
    "features.js_repl=false",
    "features.external_agent_memory_import=false",
    "features.collab=false",
    "features.collaboration_modes=false",
    "features.multi_agent=false",
    "features.multi_agent_mode=false",
    "features.multi_agent_v2=false",
    "features.enable_fanout=false",
    "features.tool_search=false",
    "features.tool_suggest=false",
    "features.search_tool=false",
    "features.responses_websockets=false",
    "features.responses_websockets_v2=false",
    "features.shell_snapshot=false",
    "features.shell_zsh_fork=false",
    "features.image_generation=false",
    "features.imagegenext=false",
    "features.view_image=false",
    "features.workspace_dependencies=false",
    "features.mcp_2026_07_28=false",
    "features.tool_call_mcp_elicitation=false",
    "features.non_prefixed_mcp_tool_names=false",
    "features.skill_search=false",
    "features.skill_env_var_dependency_prompt=false",
    "features.skill_mcp_dependency_install=false",
    "skills.include_instructions=false",
    "skills.config=[]",
    "orchestrator.mcp.enabled=false",
    "orchestrator.skills.enabled=false",
    "apps={}",
    "plugins={}",
    "mcp_servers={}",
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CodexRole {
    Generator,
    Reviewer,
}

impl CodexRole {
    pub const fn model(self) -> &'static str {
        match self {
            Self::Generator => "gpt-5.6-terra",
            Self::Reviewer => "gpt-5.6-sol",
        }
    }

    const fn as_str(self) -> &'static str {
        match self {
            Self::Generator => "generator",
            Self::Reviewer => "reviewer",
        }
    }
}

pub fn frozen_codex_argv(
    role: CodexRole,
    executable: &Path,
    schema: &Path,
    run_root: &Path,
) -> Vec<OsString> {
    let mut argv = vec![
        executable.as_os_str().to_owned(),
        "--ask-for-approval".into(),
        "never".into(),
        "exec".into(),
        "--model".into(),
        role.model().into(),
        "--strict-config".into(),
        "--ignore-user-config".into(),
        "--ignore-rules".into(),
        "--skip-git-repo-check".into(),
        "--ephemeral".into(),
        "--sandbox".into(),
        "danger-full-access".into(),
        "--output-schema".into(),
        schema.as_os_str().to_owned(),
        "--json".into(),
        "-C".into(),
        run_root.as_os_str().to_owned(),
    ];
    for value in FROZEN_CONFIG_OVERRIDES {
        argv.push("-c".into());
        argv.push((*value).into());
    }
    argv.push("-".into());
    argv
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AddressFamily {
    Ipv4,
    Ipv6,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NetworkAction {
    Permit,
    Block,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TransportProtocol {
    Tcp,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WfpRuleSpec {
    pub family: AddressFamily,
    pub action: NetworkAction,
    pub package_sid_required: bool,
    pub remote_address: Option<String>,
    pub remote_port: Option<u16>,
    pub protocol: Option<TransportProtocol>,
}

pub fn fixed_wfp_policy(proxy_port: u16) -> Result<Vec<WfpRuleSpec>, Blocker> {
    if proxy_port == 0 {
        return Err(Blocker::new(Gate::WfpTransaction, "PROXY_PORT_INVALID"));
    }
    Ok(vec![
        WfpRuleSpec {
            family: AddressFamily::Ipv4,
            action: NetworkAction::Permit,
            package_sid_required: true,
            remote_address: Some("127.0.0.1".into()),
            remote_port: Some(proxy_port),
            protocol: Some(TransportProtocol::Tcp),
        },
        WfpRuleSpec {
            family: AddressFamily::Ipv4,
            action: NetworkAction::Block,
            package_sid_required: true,
            remote_address: None,
            remote_port: None,
            protocol: None,
        },
        WfpRuleSpec {
            family: AddressFamily::Ipv6,
            action: NetworkAction::Block,
            package_sid_required: true,
            remote_address: None,
            remote_port: None,
            protocol: None,
        },
    ])
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProxyManifestBinding {
    pub canonical_json: Vec<u8>,
    pub manifest_hash: String,
    pub bind_endpoint_sha256: String,
    pub token_sha256: String,
    pub connect_allowlist: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProxyReadinessBinding {
    pub canonical_control_json: Vec<u8>,
    pub canonical_audit_json: Vec<u8>,
    pub listener_identity_sha256: String,
    pub proof_hmac_sha256: String,
}

/// Re-derives the exact canonical Python `ProxyManifest` bytes. The Basic
/// token is only consumed as bytes and is represented in the result by its
/// domain-separated digest.
pub fn canonical_proxy_manifest(
    bind_port: u16,
    connect_allowlist: &[&str],
    authorization_token: &[u8],
) -> Result<ProxyManifestBinding, Blocker> {
    if !valid_proxy_token(authorization_token) {
        return Err(Blocker::new(
            Gate::ProxyAttestation,
            "PROXY_MANIFEST_INPUT_INVALID",
        ));
    }
    canonical_proxy_manifest_from_token_hash(
        bind_port,
        connect_allowlist,
        &proxy_token_digest_bytes(authorization_token)?,
    )
}

fn canonical_proxy_manifest_from_token_hash(
    bind_port: u16,
    connect_allowlist: &[&str],
    token_sha256: &str,
) -> Result<ProxyManifestBinding, Blocker> {
    if bind_port == 0
        || !is_sha256_digest(token_sha256)
        || connect_allowlist.is_empty()
        || connect_allowlist.len() > 2
        || connect_allowlist.windows(2).any(|pair| pair[0] >= pair[1])
        || connect_allowlist
            .iter()
            .any(|value| !matches!(*value, "api.openai.com:443" | "chatgpt.com:443"))
    {
        return Err(Blocker::new(
            Gate::ProxyAttestation,
            "PROXY_MANIFEST_INPUT_INVALID",
        ));
    }
    let allowlist_json = connect_allowlist
        .iter()
        .map(|value| format!("\"{value}\""))
        .collect::<Vec<_>>()
        .join(",");
    let payload = format!(
        "{{\"authorization_mode\":\"BASIC_USERINFO_ENV_ONLY\",\"authorization_username_sha256\":\"{PROXY_USERNAME_SHA256}\",\"bind_host\":\"127.0.0.1\",\"bind_port\":{bind_port},\"connect_allowlist\":[{allowlist_json}],\"deadline_milliseconds\":900000,\"listener_mode\":\"BROKER_PREBOUND_INHERITED\",\"max_client_hello_bytes\":32768,\"max_connections\":1,\"max_header_bytes\":8192,\"max_tunnel_bytes\":1048576,\"schema_version\":\"t13b-loopback-connect-v1\",\"token_sha256\":\"{token_sha256}\"}}"
    );
    let manifest_hash = sha256_digest(payload.as_bytes())?;
    let canonical_json = format!(
        "{{\"authorization_mode\":\"BASIC_USERINFO_ENV_ONLY\",\"authorization_username_sha256\":\"{PROXY_USERNAME_SHA256}\",\"bind_host\":\"127.0.0.1\",\"bind_port\":{bind_port},\"connect_allowlist\":[{allowlist_json}],\"deadline_milliseconds\":900000,\"listener_mode\":\"BROKER_PREBOUND_INHERITED\",\"manifest_hash\":\"{manifest_hash}\",\"max_client_hello_bytes\":32768,\"max_connections\":1,\"max_header_bytes\":8192,\"max_tunnel_bytes\":1048576,\"schema_version\":\"t13b-loopback-connect-v1\",\"token_sha256\":\"{token_sha256}\"}}"
    )
    .into_bytes();
    let bind_endpoint_sha256 = sha256_digest(format!("127.0.0.1:{bind_port}").as_bytes())?;
    Ok(ProxyManifestBinding {
        canonical_json,
        manifest_hash,
        bind_endpoint_sha256,
        token_sha256: token_sha256.to_owned(),
        connect_allowlist: connect_allowlist
            .iter()
            .map(|value| (*value).to_owned())
            .collect(),
    })
}

/// Verifies the exact canonical Python `ProxyReadiness` control bytes and
/// returns its hash-only audit representation. This is a control-channel
/// proof; it never connects to or consumes the one-shot listener.
pub fn canonical_proxy_readiness(
    manifest: &ProxyManifestBinding,
    authorization_token: &[u8],
    challenge: &[u8],
    process_id: u32,
    listener_handle: u64,
) -> Result<ProxyReadinessBinding, Blocker> {
    if !valid_proxy_token(authorization_token)
        || proxy_token_digest_bytes(authorization_token)? != manifest.token_sha256
        || challenge.len() != 32
        || process_id == 0
    {
        return Err(Blocker::new(
            Gate::ProxyAttestation,
            "PROXY_READINESS_INPUT_INVALID",
        ));
    }
    let challenge_sha256 = sha256_digest(challenge)?;
    let identity = format!(
        "{{\"bind_endpoint_sha256\":\"{}\",\"challenge_sha256\":\"{challenge_sha256}\",\"listener_handle\":{listener_handle},\"manifest_hash\":\"{}\",\"process_id\":{process_id},\"schema_version\":\"t13b-proxy-readiness-v1\"}}",
        manifest.bind_endpoint_sha256, manifest.manifest_hash,
    );
    let listener_identity_sha256 = sha256_digest(identity.as_bytes())?;
    let proof_payload = format!(
        "{{\"bind_endpoint_sha256\":\"{}\",\"challenge_sha256\":\"{challenge_sha256}\",\"listener_handle\":{listener_handle},\"listener_identity_sha256\":\"{listener_identity_sha256}\",\"manifest_hash\":\"{}\",\"process_id\":{process_id},\"schema_version\":\"t13b-proxy-readiness-v1\"}}",
        manifest.bind_endpoint_sha256, manifest.manifest_hash,
    );
    let mut proof_input =
        Vec::with_capacity(PROXY_READINESS_HMAC_DOMAIN.len() + proof_payload.len());
    proof_input.extend_from_slice(PROXY_READINESS_HMAC_DOMAIN);
    proof_input.extend_from_slice(proof_payload.as_bytes());
    let proof_hmac_sha256 = hmac_sha256_digest(authorization_token, &proof_input)?;
    let expected_control = format!(
        "{{\"bind_endpoint_sha256\":\"{}\",\"challenge_sha256\":\"{challenge_sha256}\",\"listener_handle\":{listener_handle},\"listener_identity_sha256\":\"{listener_identity_sha256}\",\"manifest_hash\":\"{}\",\"process_id\":{process_id},\"proof_hmac_sha256\":\"{proof_hmac_sha256}\",\"schema_version\":\"t13b-proxy-readiness-v1\"}}",
        manifest.bind_endpoint_sha256, manifest.manifest_hash,
    )
    .into_bytes();
    let canonical_audit_json = format!(
        "{{\"bind_endpoint_sha256\":\"{}\",\"listener_identity_sha256\":\"{listener_identity_sha256}\",\"manifest_hash\":\"{}\",\"proof_hmac_sha256\":\"{proof_hmac_sha256}\",\"schema_version\":\"t13b-proxy-readiness-v1\"}}",
        manifest.bind_endpoint_sha256, manifest.manifest_hash,
    )
    .into_bytes();
    Ok(ProxyReadinessBinding {
        canonical_control_json: expected_control,
        canonical_audit_json,
        listener_identity_sha256,
        proof_hmac_sha256,
    })
}

pub fn verify_proxy_readiness(
    manifest: &ProxyManifestBinding,
    authorization_token: &[u8],
    challenge: &[u8],
    process_id: u32,
    listener_handle: u64,
    control_json: &[u8],
) -> Result<ProxyReadinessBinding, Blocker> {
    let expected = canonical_proxy_readiness(
        manifest,
        authorization_token,
        challenge,
        process_id,
        listener_handle,
    )?;
    if !constant_time_eq(control_json, &expected.canonical_control_json) {
        return Err(Blocker::new(
            Gate::ProxyAttestation,
            "PROXY_READINESS_PROOF_MISMATCH",
        ));
    }
    Ok(expected)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LaunchAttributesSpec {
    pub create_suspended: bool,
    pub security_capabilities: bool,
    pub all_application_packages_opt_out: bool,
    pub restrict_child_processes: bool,
    pub atomic_job_list: bool,
    pub explicit_handle_allowlist: bool,
    pub breakaway_allowed: bool,
}

pub const fn fixed_launch_attributes() -> LaunchAttributesSpec {
    LaunchAttributesSpec {
        create_suspended: true,
        security_capabilities: true,
        all_application_packages_opt_out: true,
        restrict_child_processes: true,
        atomic_job_list: true,
        explicit_handle_allowlist: true,
        breakaway_allowed: false,
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Gate {
    Manifest,
    Elevation,
    BinaryIdentity,
    AppContainerLpac,
    LoopbackExemption,
    ProxyAttestation,
    WfpTransaction,
    FileBoundary,
    JobPrepared,
    PrelaunchObserved,
    ChildCreatedSuspended,
    ChildResumed,
    OutputAndDeadline,
    JobTerminated,
    JobDrained,
    FileBoundaryRestored,
    LoopbackExemptionRemoved,
    WfpPolicyRemoved,
    ProxyStopped,
    AppContainerRemoved,
    CleanupFinalized,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Blocker {
    pub gate: Gate,
    pub code: &'static str,
}

impl Blocker {
    pub const fn new(gate: Gate, code: &'static str) -> Self {
        Self { gate, code }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Terminal {
    ContractHarnessCompletedUnqualified,
    BlockedPrequalification,
}

#[derive(Debug, Eq, PartialEq)]
pub struct PrelaunchObservation {
    pub windows_major: u32,
    pub windows_minor: u32,
    pub windows_build: u32,
    pub windows_ubr: u32,
    pub architecture: String,
    pub appcontainer_profile_name: String,
    pub package_sid: String,
    pub capability_sids: Vec<String>,
    pub same_sid_processes_before: u32,
    pub wfp_filter_ids: Vec<u64>,
    pub wfp_intent_sha256: String,
    pub wfp_readback_sha256: String,
    pub wfp_negative_matrix_sha256: String,
    pub loopback_state_before_sha256: String,
    pub loopback_state_active_sha256: String,
    pub proxy_process_identity_sha256: String,
    pub parser_fixture_sha256: String,
    pub maintenance_lock_identity_sha256: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct JobDrainProof {
    pub descendant_count: u32,
    pub same_sid_processes_after_job_drain: u32,
    pub job_accounting_sha256: String,
}

#[derive(Debug, Eq, PartialEq)]
pub struct PostCleanupProof {
    pub loopback_state_restored_sha256: String,
    pub wfp_state_restored_sha256: String,
    pub proxy_terminal_sha256: String,
    pub file_boundary_restored_sha256: String,
    pub appcontainer_profile_removed_sha256: String,
    pub maintenance_lock_released_sha256: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum SensitiveOutputDisposition {
    #[default]
    NotScanned,
    Clean,
    SecretDetected,
}

#[derive(Debug, Eq, PartialEq)]
pub struct ProcessOutcome {
    pub exit_code: Option<u32>,
    pub timed_out: bool,
    pub stdout_bytes: usize,
    pub stderr_bytes: usize,
    pub network_or_auth_used: Option<bool>,
    pub stdout_artifact: Option<CapturedOutputArtifact>,
    pub stderr_artifact: Option<CapturedOutputArtifact>,
    pub sensitive_output_disposition: SensitiveOutputDisposition,
    pub sensitive_output_scan_sha256: String,
    pub file_trace_sha256: String,
    pub process_trace_sha256: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FileCreationAuthority {
    OfficialReleaseCopy,
    BrokerCreateNew,
    NativeCreateNewOutput,
}

impl FileCreationAuthority {
    const fn as_str(self) -> &'static str {
        match self {
            Self::OfficialReleaseCopy => "OFFICIAL_RELEASE_COPY",
            Self::BrokerCreateNew => "BROKER_CREATE_NEW",
            Self::NativeCreateNewOutput => "NATIVE_CREATE_NEW_OUTPUT",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NtfsFileIdentity {
    pub path: PathBuf,
    pub volume_serial: u64,
    pub file_id: [u8; 16],
    pub parent_file_id: [u8; 16],
    pub length: u64,
    pub sha256: String,
    pub link_count: u32,
    pub creation_authority: FileCreationAuthority,
}

#[derive(Eq, PartialEq)]
pub struct CapturedOutputArtifact {
    pub identity: NtfsFileIdentity,
    pub bytes: Vec<u8>,
    pub sha256: String,
    pub truncated: bool,
}

impl std::fmt::Debug for CapturedOutputArtifact {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CapturedOutputArtifact")
            .field("identity", &self.identity)
            .field("length", &self.bytes.len())
            .field("sha256", &self.sha256)
            .field("truncated", &self.truncated)
            .finish()
    }
}

impl Default for ProcessOutcome {
    fn default() -> Self {
        Self {
            exit_code: Some(0),
            timed_out: false,
            stdout_bytes: 0,
            stderr_bytes: 0,
            network_or_auth_used: Some(false),
            stdout_artifact: None,
            stderr_artifact: None,
            sensitive_output_disposition: SensitiveOutputDisposition::NotScanned,
            sensitive_output_scan_sha256: String::new(),
            file_trace_sha256: String::new(),
            process_trace_sha256: String::new(),
        }
    }
}

#[derive(Eq, PartialEq)]
pub struct QualificationPlan {
    pub binary_path: PathBuf,
    pub binary_sha256: String,
    pub expected_version: String,
    pub runtime_root: PathBuf,
    pub codex_home: PathBuf,
    pub run_root: PathBuf,
    pub evidence_path: PathBuf,
    pub schema_path: PathBuf,
    pub prompt_path: PathBuf,
    pub binary_identity: NtfsFileIdentity,
    pub evidence_identity: NtfsFileIdentity,
    pub schema_identity: NtfsFileIdentity,
    pub prompt_identity: NtfsFileIdentity,
    pub stdin_bytes: Vec<u8>,
    pub stdin_sha256: String,
    pub stdin_length: u64,
    pub manifest_bytes: Vec<u8>,
    pub manifest_sha256: String,
    pub role: CodexRole,
    pub model: String,
    pub argv: Vec<OsString>,
    pub environment: BTreeMap<String, OsString>,
    pub proxy_username: String,
    pub proxy_token_sha256: String,
    pub proxy_port: u16,
    pub proxy_connect_allowlist: Vec<String>,
    pub proxy_manifest_json: Vec<u8>,
    pub proxy_manifest_sha256: String,
    pub proxy_manifest_hash: String,
    pub proxy_bind_endpoint_sha256: String,
    pub proxy_readiness_control_json: Vec<u8>,
    pub proxy_readiness_sha256: String,
    pub proxy_readiness_audit_json: Vec<u8>,
    pub proxy_readiness_audit_sha256: String,
    pub proxy_listener_identity_sha256: String,
    pub proxy_readiness_hmac_sha256: String,
    pub proxy_challenge: [u8; 32],
    pub proxy_challenge_sha256: String,
    pub proxy_process_id: u32,
    pub proxy_listener_handle: u64,
    pub deadline: Duration,
    pub stdout_limit: usize,
    pub stderr_limit: usize,
}

impl QualificationPlan {
    pub fn bind_stdin(&mut self, stdin: &[u8]) -> Result<(), Blocker> {
        if !self.stdin_bytes.is_empty()
            || !self.stdin_sha256.is_empty()
            || self.stdin_length != 0
            || stdin.is_empty()
            || stdin.len() > MAX_STDIN_BYTES
            || stdin.contains(&0)
        {
            return Err(Blocker::new(Gate::Manifest, "STDIN_BINDING_INVALID"));
        }
        self.stdin_bytes = stdin.to_vec();
        self.stdin_length = stdin.len() as u64;
        self.stdin_sha256 = sha256_digest(stdin)?;
        Ok(())
    }

    pub fn bind_proxy_secret_hash(&mut self, username: &str, token: &[u8]) -> Result<(), Blocker> {
        if !self.proxy_username.is_empty()
            || !self.proxy_token_sha256.is_empty()
            || username != "pkm-t13b"
            || !valid_proxy_token(token)
        {
            return Err(Blocker::new(Gate::Manifest, "PROXY_SECRET_BINDING_INVALID"));
        }
        self.proxy_username = username.to_owned();
        self.proxy_token_sha256 = proxy_token_digest_bytes(token)?;
        Ok(())
    }

    pub fn bind_proxy_control(
        &mut self,
        connect_allowlist: &[&str],
        authorization_token: &[u8],
        challenge: [u8; 32],
        process_id: u32,
        listener_handle: u64,
        readiness_control_json: &[u8],
    ) -> Result<(), Blocker> {
        if !self.proxy_connect_allowlist.is_empty()
            || !self.proxy_manifest_json.is_empty()
            || !self.proxy_manifest_sha256.is_empty()
            || !self.proxy_manifest_hash.is_empty()
            || !self.proxy_bind_endpoint_sha256.is_empty()
            || !self.proxy_readiness_control_json.is_empty()
            || !self.proxy_readiness_sha256.is_empty()
            || !self.proxy_readiness_audit_json.is_empty()
            || !self.proxy_readiness_audit_sha256.is_empty()
            || !self.proxy_listener_identity_sha256.is_empty()
            || !self.proxy_readiness_hmac_sha256.is_empty()
            || self.proxy_challenge != [0; 32]
            || !self.proxy_challenge_sha256.is_empty()
            || self.proxy_process_id != 0
            || self.proxy_listener_handle != 0
        {
            return Err(Blocker::new(
                Gate::ProxyAttestation,
                "PROXY_CONTROL_ALREADY_BOUND",
            ));
        }
        if proxy_token_digest_bytes(authorization_token)? != self.proxy_token_sha256 {
            return Err(Blocker::new(
                Gate::ProxyAttestation,
                "PROXY_CONTROL_TOKEN_MISMATCH",
            ));
        }
        let manifest =
            canonical_proxy_manifest(self.proxy_port, connect_allowlist, authorization_token)?;
        let readiness = verify_proxy_readiness(
            &manifest,
            authorization_token,
            &challenge,
            process_id,
            listener_handle,
            readiness_control_json,
        )?;
        self.proxy_connect_allowlist = manifest.connect_allowlist;
        self.proxy_manifest_sha256 = sha256_digest(&manifest.canonical_json)?;
        self.proxy_manifest_hash = manifest.manifest_hash;
        self.proxy_bind_endpoint_sha256 = manifest.bind_endpoint_sha256;
        self.proxy_manifest_json = manifest.canonical_json;
        self.proxy_readiness_sha256 = sha256_digest(&readiness.canonical_control_json)?;
        self.proxy_readiness_audit_sha256 = sha256_digest(&readiness.canonical_audit_json)?;
        self.proxy_readiness_control_json = readiness.canonical_control_json;
        self.proxy_readiness_audit_json = readiness.canonical_audit_json;
        self.proxy_listener_identity_sha256 = readiness.listener_identity_sha256;
        self.proxy_readiness_hmac_sha256 = readiness.proof_hmac_sha256;
        self.proxy_challenge = challenge;
        self.proxy_challenge_sha256 = sha256_digest(&challenge)?;
        self.proxy_process_id = process_id;
        self.proxy_listener_handle = listener_handle;
        Ok(())
    }

    pub fn seal_manifest(&mut self) -> Result<(), Blocker> {
        self.manifest_bytes = self.canonical_manifest_bytes()?;
        self.manifest_sha256 = sha256_digest(&self.manifest_bytes)?;
        Ok(())
    }

    /// Content-addressed closure for audit/replay. This is deliberately not a
    /// signature and conveys no authority beyond the bytes re-derived here.
    pub fn intent_closure_sha256(&self) -> Result<String, Blocker> {
        let mut input = b"PKM-CODEX-T13B-INTENT-CLOSURE-V1\0".to_vec();
        input.extend_from_slice(self.manifest_sha256.as_bytes());
        sha256_digest(&input)
    }

    fn validate(&self) -> Result<(), Blocker> {
        if self.binary_sha256 != EXPECTED_CODEX_EXE_SHA256
            || self.expected_version != EXPECTED_CODEX_VERSION
            || self.proxy_port == 0
        {
            return Err(Blocker::new(Gate::Manifest, "FROZEN_IDENTITY_DRIFT"));
        }
        if self.deadline.is_zero()
            || self.deadline > MAX_DEADLINE
            || self.stdout_limit == 0
            || self.stdout_limit > MAX_STDOUT_BYTES
            || self.stderr_limit == 0
            || self.stderr_limit > MAX_STDERR_BYTES
        {
            return Err(Blocker::new(Gate::Manifest, "RESOURCE_LIMIT_DRIFT"));
        }
        validate_paths(self)?;
        validate_file_identities(self)?;
        if self.stdin_bytes.is_empty()
            || self.stdin_bytes.len() > MAX_STDIN_BYTES
            || self.stdin_length != self.stdin_bytes.len() as u64
            || self.stdin_sha256 != sha256_digest(&self.stdin_bytes)?
        {
            return Err(Blocker::new(Gate::Manifest, "STDIN_BINDING_MISMATCH"));
        }
        validate_environment(self)?;
        validate_proxy_control(self)?;
        validate_argv(self)?;

        let expected_manifest = self.canonical_manifest_bytes()?;
        if self.manifest_bytes != expected_manifest
            || self.manifest_sha256 != sha256_digest(&expected_manifest)?
        {
            return Err(Blocker::new(Gate::Manifest, "MANIFEST_BINDING_MISMATCH"));
        }
        Ok(())
    }

    fn canonical_manifest_bytes(&self) -> Result<Vec<u8>, Blocker> {
        let mut output = MANIFEST_DOMAIN.to_vec();
        append_os(&mut output, self.binary_path.as_os_str())?;
        append_bytes(&mut output, self.binary_sha256.as_bytes());
        append_bytes(&mut output, self.expected_version.as_bytes());
        append_os(&mut output, self.runtime_root.as_os_str())?;
        append_os(&mut output, self.codex_home.as_os_str())?;
        append_os(&mut output, self.run_root.as_os_str())?;
        append_os(&mut output, self.evidence_path.as_os_str())?;
        append_os(&mut output, self.schema_path.as_os_str())?;
        append_os(&mut output, self.prompt_path.as_os_str())?;
        append_file_identity(&mut output, &self.binary_identity)?;
        append_file_identity(&mut output, &self.evidence_identity)?;
        append_file_identity(&mut output, &self.schema_identity)?;
        append_file_identity(&mut output, &self.prompt_identity)?;
        append_bytes(&mut output, &self.stdin_length.to_le_bytes());
        append_bytes(&mut output, self.stdin_sha256.as_bytes());
        append_bytes(&mut output, &self.proxy_port.to_le_bytes());
        append_bytes(&mut output, &self.deadline.as_millis().to_le_bytes());
        append_bytes(&mut output, &self.stdout_limit.to_le_bytes());
        append_bytes(&mut output, &self.stderr_limit.to_le_bytes());
        append_bytes(&mut output, self.role.as_str().as_bytes());
        append_bytes(&mut output, self.model.as_bytes());
        append_bytes(&mut output, &(self.argv.len() as u64).to_le_bytes());
        for arg in &self.argv {
            append_os(&mut output, arg)?;
        }
        append_bytes(&mut output, &(self.environment.len() as u64).to_le_bytes());
        append_bytes(&mut output, self.proxy_username.as_bytes());
        append_bytes(&mut output, self.proxy_token_sha256.as_bytes());
        append_bytes(&mut output, self.proxy_manifest_sha256.as_bytes());
        append_bytes(&mut output, self.proxy_manifest_hash.as_bytes());
        append_bytes(&mut output, self.proxy_bind_endpoint_sha256.as_bytes());
        append_bytes(&mut output, self.proxy_readiness_sha256.as_bytes());
        append_bytes(&mut output, self.proxy_readiness_audit_sha256.as_bytes());
        append_bytes(&mut output, self.proxy_listener_identity_sha256.as_bytes());
        append_bytes(&mut output, self.proxy_readiness_hmac_sha256.as_bytes());
        append_bytes(&mut output, self.proxy_challenge_sha256.as_bytes());
        append_bytes(&mut output, &self.proxy_process_id.to_le_bytes());
        append_bytes(&mut output, &self.proxy_listener_handle.to_le_bytes());
        append_bytes(
            &mut output,
            &(self.proxy_connect_allowlist.len() as u64).to_le_bytes(),
        );
        for authority in &self.proxy_connect_allowlist {
            append_bytes(&mut output, authority.as_bytes());
        }
        for (key, value) in &self.environment {
            append_bytes(&mut output, key.as_bytes());
            append_os(&mut output, value)?;
        }
        Ok(output)
    }
}

#[derive(Debug, Eq, PartialEq)]
pub struct QualificationReport {
    pub terminal: Terminal,
    pub blocker: Option<Blocker>,
    pub secondary_blocker: Option<Blocker>,
    pub cleanup_blockers: Vec<Blocker>,
    pub secret_materialized_in_child: bool,
    pub network_or_auth_used: Option<bool>,
    pub child_created: bool,
    pub child_resumed: bool,
    pub manifest_sha256: Option<String>,
    pub intent_closure_sha256: Option<String>,
    pub os_qualification_closure_sha256: Option<String>,
    pub result_closure_sha256: Option<String>,
    pub prelaunch_observation: Option<PrelaunchObservation>,
    pub failed_drain_proof: Option<JobDrainProof>,
    pub drain_proof: Option<JobDrainProof>,
    pub post_cleanup_proof: Option<PostCleanupProof>,
    pub outcome: Option<ProcessOutcome>,
}

impl QualificationReport {
    fn blocked(
        blocker: Blocker,
        plan: &QualificationPlan,
        child_created: bool,
        child_resumed: bool,
        secret_materialized_in_child: bool,
        network_or_auth_used: Option<bool>,
    ) -> Self {
        Self {
            terminal: Terminal::BlockedPrequalification,
            blocker: Some(blocker),
            secondary_blocker: None,
            cleanup_blockers: Vec::new(),
            secret_materialized_in_child,
            network_or_auth_used,
            child_created,
            child_resumed,
            manifest_sha256: nonempty(&plan.manifest_sha256),
            intent_closure_sha256: plan.intent_closure_sha256().ok(),
            os_qualification_closure_sha256: None,
            result_closure_sha256: None,
            prelaunch_observation: None,
            failed_drain_proof: None,
            drain_proof: None,
            post_cleanup_proof: None,
            outcome: None,
        }
    }
}

pub trait IsolationBackend {
    fn require_elevation(&mut self) -> Result<(), Blocker>;
    fn verify_binary(&mut self, plan: &QualificationPlan) -> Result<(), Blocker>;
    fn prepare_lpac_profile(&mut self, plan: &QualificationPlan) -> Result<(), Blocker>;
    fn install_loopback_exemption(&mut self, plan: &QualificationPlan) -> Result<(), Blocker>;
    fn attest_proxy(&mut self, plan: &QualificationPlan) -> Result<(), Blocker>;
    fn install_wfp_policy(&mut self, plan: &QualificationPlan) -> Result<(), Blocker>;
    fn verify_file_boundary(&mut self, plan: &QualificationPlan) -> Result<(), Blocker>;
    fn prepare_job(&mut self, plan: &QualificationPlan) -> Result<(), Blocker>;
    fn observe_prelaunch(
        &mut self,
        plan: &QualificationPlan,
    ) -> Result<PrelaunchObservation, Blocker>;
    fn create_suspended(&mut self, plan: &QualificationPlan) -> Result<(), Blocker>;
    fn resume(&mut self) -> Result<(), Blocker>;
    fn wait_and_capture(&mut self, plan: &QualificationPlan) -> Result<ProcessOutcome, Blocker>;
    fn terminate_job(&mut self) -> Result<(), Blocker>;
    fn drain_job(&mut self) -> Result<JobDrainProof, Blocker>;
    fn rollback_file_boundary(&mut self) -> Result<(), Blocker>;
    fn remove_loopback_exemption(&mut self) -> Result<(), Blocker>;
    fn remove_wfp_policy(&mut self) -> Result<(), Blocker>;
    fn stop_proxy(&mut self) -> Result<(), Blocker>;
    fn remove_lpac_profile(&mut self) -> Result<(), Blocker>;
    fn finalize_cleanup(
        &mut self,
        plan: &QualificationPlan,
        drain: Option<&JobDrainProof>,
    ) -> Result<Option<PostCleanupProof>, Blocker>;
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct AcquiredResources {
    profile: bool,
    proxy: bool,
    wfp: bool,
    loopback_exemption: bool,
    file_boundary: bool,
    job: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ExecutionEvidence {
    child_created: bool,
    child_resumed: bool,
    secret_materialized_in_child: bool,
    network_or_auth_used: Option<bool>,
}

impl ExecutionEvidence {
    const PRE_CHILD: Self = Self {
        child_created: false,
        child_resumed: false,
        secret_materialized_in_child: false,
        network_or_auth_used: Some(false),
    };

    const CREATE_ATTEMPTED: Self = Self {
        // A backend error can occur after the OS created a process but before
        // post-create verification returned. Conservatively record creation;
        // the native implementation must use atomic JOB_LIST assignment and
        // keep process/thread handles in an RAII transaction.
        child_created: true,
        child_resumed: false,
        secret_materialized_in_child: true,
        network_or_auth_used: Some(false),
    };

    const RESUME_ATTEMPTED: Self = Self {
        child_created: true,
        // ResumeThread can succeed before a later verification fails. Treat
        // any resume error as possibly executed/networked until trace proves
        // otherwise.
        child_resumed: true,
        secret_materialized_in_child: true,
        network_or_auth_used: None,
    };

    const fn resumed(network_or_auth_used: Option<bool>) -> Self {
        Self {
            child_created: true,
            child_resumed: true,
            secret_materialized_in_child: true,
            network_or_auth_used,
        }
    }
}

pub struct QualificationEngine;

impl QualificationEngine {
    pub fn qualify<B: IsolationBackend>(
        plan: &QualificationPlan,
        backend: &mut B,
    ) -> QualificationReport {
        if let Err(blocker) = plan.validate() {
            return QualificationReport::blocked(blocker, plan, false, false, false, Some(false));
        }

        if let Err(blocker) = backend.require_elevation() {
            return QualificationReport::blocked(blocker, plan, false, false, false, Some(false));
        }
        if let Err(blocker) = backend.verify_binary(plan) {
            return QualificationReport::blocked(blocker, plan, false, false, false, Some(false));
        }
        let mut acquired = AcquiredResources {
            profile: true,
            ..AcquiredResources::default()
        };
        if let Err(blocker) = backend.prepare_lpac_profile(plan) {
            return cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::PRE_CHILD,
            );
        }
        acquired.proxy = true;
        if let Err(blocker) = backend.attest_proxy(plan) {
            return cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::PRE_CHILD,
            );
        }
        acquired.wfp = true;
        if let Err(blocker) = backend.install_wfp_policy(plan) {
            return cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::PRE_CHILD,
            );
        }
        acquired.loopback_exemption = true;
        if let Err(blocker) = backend.install_loopback_exemption(plan) {
            return cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::PRE_CHILD,
            );
        }
        acquired.file_boundary = true;
        if let Err(blocker) = backend.verify_file_boundary(plan) {
            return cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::PRE_CHILD,
            );
        }
        acquired.job = true;
        if let Err(blocker) = backend.prepare_job(plan) {
            return cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::PRE_CHILD,
            );
        }
        let prelaunch_observation = match backend.observe_prelaunch(plan) {
            Ok(observation) => observation,
            Err(blocker) => {
                return cleanup_failure(
                    backend,
                    plan,
                    acquired,
                    blocker,
                    ExecutionEvidence::PRE_CHILD,
                );
            }
        };
        if let Err(blocker) = validate_prelaunch_observation(plan, &prelaunch_observation) {
            let mut report = cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::PRE_CHILD,
            );
            report.prelaunch_observation = Some(prelaunch_observation);
            return report;
        }
        if let Err(blocker) = backend.create_suspended(plan) {
            // Even an atomic create failure may occur after the secret-bearing
            // environment block was materialized transiently. Report the
            // conservative historical exposure while cleanup proves no live
            // child remains.
            return cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::CREATE_ATTEMPTED,
            );
        }

        if let Err(blocker) = backend.resume() {
            return cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::RESUME_ATTEMPTED,
            );
        }
        let outcome = match backend.wait_and_capture(plan) {
            Ok(outcome) => outcome,
            Err(blocker) => {
                return cleanup_failure(
                    backend,
                    plan,
                    acquired,
                    blocker,
                    ExecutionEvidence::resumed(None),
                );
            }
        };

        if let Some(blocker) = outcome_blocker(&outcome, plan) {
            let mut report = cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::resumed(outcome.network_or_auth_used),
            );
            report.prelaunch_observation = Some(prelaunch_observation);
            report.outcome = Some(outcome);
            return report;
        }
        let drain_proof = match backend.drain_job() {
            Ok(proof) => proof,
            Err(blocker) => {
                let mut report = cleanup_failure(
                    backend,
                    plan,
                    acquired,
                    blocker,
                    ExecutionEvidence::resumed(outcome.network_or_auth_used),
                );
                report.prelaunch_observation = Some(prelaunch_observation);
                report.outcome = Some(outcome);
                return report;
            }
        };
        if let Err(blocker) = validate_drain_proof(&drain_proof) {
            let mut report = cleanup_failure(
                backend,
                plan,
                acquired,
                blocker,
                ExecutionEvidence::resumed(outcome.network_or_auth_used),
            );
            report.prelaunch_observation = Some(prelaunch_observation);
            report.failed_drain_proof = Some(drain_proof);
            report.outcome = Some(outcome);
            return report;
        }
        acquired.job = false;
        let cleanup = cleanup_resources(backend, plan, acquired, false, Some(drain_proof.clone()));
        if let Some(blocker) = cleanup.blockers.first().cloned() {
            let mut report = QualificationReport::blocked(
                blocker,
                plan,
                true,
                true,
                true,
                outcome.network_or_auth_used,
            );
            report.cleanup_blockers = cleanup.blockers;
            report.prelaunch_observation = Some(prelaunch_observation);
            report.drain_proof = Some(drain_proof);
            report.outcome = Some(outcome);
            return report;
        }
        let Some(post_cleanup_proof) = cleanup.post_cleanup_proof else {
            let mut report = QualificationReport::blocked(
                Blocker::new(Gate::CleanupFinalized, "POST_CLEANUP_PROOF_MISSING"),
                plan,
                true,
                true,
                true,
                outcome.network_or_auth_used,
            );
            report.prelaunch_observation = Some(prelaunch_observation);
            report.drain_proof = Some(drain_proof);
            report.outcome = Some(outcome);
            return report;
        };
        if let Err(blocker) = validate_qualification_evidence(
            plan,
            &prelaunch_observation,
            &outcome,
            &drain_proof,
            &post_cleanup_proof,
        ) {
            let mut report = QualificationReport::blocked(
                blocker,
                plan,
                true,
                true,
                true,
                outcome.network_or_auth_used,
            );
            report.prelaunch_observation = Some(prelaunch_observation);
            report.drain_proof = Some(drain_proof);
            report.post_cleanup_proof = Some(post_cleanup_proof);
            report.outcome = Some(outcome);
            return report;
        }
        let intent_closure = match plan.intent_closure_sha256() {
            Ok(value) => value,
            Err(blocker) => {
                return completed_evidence_blocked(
                    plan,
                    blocker,
                    prelaunch_observation,
                    outcome,
                    drain_proof,
                    post_cleanup_proof,
                );
            }
        };
        let os_closure = match os_qualification_closure_sha256(
            plan,
            &prelaunch_observation,
            &outcome,
            &drain_proof,
            &post_cleanup_proof,
        ) {
            Ok(value) => value,
            Err(blocker) => {
                return completed_evidence_blocked(
                    plan,
                    blocker,
                    prelaunch_observation,
                    outcome,
                    drain_proof,
                    post_cleanup_proof,
                );
            }
        };
        let result_closure = match result_closure_sha256(
            plan,
            &prelaunch_observation,
            &outcome,
            &drain_proof,
            &post_cleanup_proof,
        ) {
            Ok(value) => value,
            Err(blocker) => {
                return completed_evidence_blocked(
                    plan,
                    blocker,
                    prelaunch_observation,
                    outcome,
                    drain_proof,
                    post_cleanup_proof,
                );
            }
        };

        QualificationReport {
            terminal: Terminal::ContractHarnessCompletedUnqualified,
            blocker: None,
            secondary_blocker: None,
            cleanup_blockers: Vec::new(),
            secret_materialized_in_child: true,
            network_or_auth_used: outcome.network_or_auth_used,
            child_created: true,
            child_resumed: true,
            manifest_sha256: Some(plan.manifest_sha256.clone()),
            intent_closure_sha256: Some(intent_closure),
            os_qualification_closure_sha256: Some(os_closure),
            result_closure_sha256: Some(result_closure),
            prelaunch_observation: Some(prelaunch_observation),
            failed_drain_proof: None,
            drain_proof: Some(drain_proof),
            post_cleanup_proof: Some(post_cleanup_proof),
            outcome: Some(outcome),
        }
    }
}

fn completed_evidence_blocked(
    plan: &QualificationPlan,
    blocker: Blocker,
    prelaunch_observation: PrelaunchObservation,
    outcome: ProcessOutcome,
    drain_proof: JobDrainProof,
    post_cleanup_proof: PostCleanupProof,
) -> QualificationReport {
    let mut report = QualificationReport::blocked(
        blocker,
        plan,
        true,
        true,
        true,
        outcome.network_or_auth_used,
    );
    report.prelaunch_observation = Some(prelaunch_observation);
    report.outcome = Some(outcome);
    report.drain_proof = Some(drain_proof);
    report.post_cleanup_proof = Some(post_cleanup_proof);
    report
}

fn cleanup_failure<B: IsolationBackend>(
    backend: &mut B,
    plan: &QualificationPlan,
    acquired: AcquiredResources,
    original: Blocker,
    execution: ExecutionEvidence,
) -> QualificationReport {
    let cleanup = cleanup_resources(backend, plan, acquired, acquired.job, None);
    let CleanupResult {
        blockers,
        failed_drain_proof,
        drain_proof,
        post_cleanup_proof,
    } = cleanup;
    if let Some(blocker) = blockers.first().cloned() {
        let mut report = QualificationReport::blocked(
            blocker,
            plan,
            execution.child_created,
            execution.child_resumed,
            execution.secret_materialized_in_child,
            execution.network_or_auth_used,
        );
        report.secondary_blocker = Some(original);
        report.cleanup_blockers = blockers;
        report.failed_drain_proof = failed_drain_proof;
        report.drain_proof = drain_proof;
        report.post_cleanup_proof = post_cleanup_proof;
        report
    } else {
        let mut report = QualificationReport::blocked(
            original,
            plan,
            execution.child_created,
            execution.child_resumed,
            execution.secret_materialized_in_child,
            execution.network_or_auth_used,
        );
        report.failed_drain_proof = failed_drain_proof;
        report.drain_proof = drain_proof;
        report.post_cleanup_proof = post_cleanup_proof;
        report
    }
}

#[derive(Debug, Default)]
struct CleanupResult {
    blockers: Vec<Blocker>,
    failed_drain_proof: Option<JobDrainProof>,
    drain_proof: Option<JobDrainProof>,
    post_cleanup_proof: Option<PostCleanupProof>,
}

fn cleanup_resources<B: IsolationBackend>(
    backend: &mut B,
    plan: &QualificationPlan,
    acquired: AcquiredResources,
    terminate_job: bool,
    mut drain_proof: Option<JobDrainProof>,
) -> CleanupResult {
    let mut result = CleanupResult::default();
    if acquired.job {
        if terminate_job {
            if let Err(blocker) = backend.terminate_job() {
                result.blockers.push(blocker);
                // A failed termination provides no proof that descendants are
                // gone. Preserve every restrictive boundary and the lock.
                return result;
            }
        }
        match backend.drain_job() {
            Ok(proof) => {
                if let Err(blocker) = validate_drain_proof(&proof) {
                    result.failed_drain_proof = Some(proof);
                    result.blockers.push(blocker);
                    return result;
                }
                result.drain_proof = Some(proof.clone());
                drain_proof = Some(proof);
            }
            Err(blocker) => {
                result.blockers.push(blocker);
                // Never remove the loopback exemption/WFP boundary while a
                // child tree may still be alive. Leaking restrictive resources
                // is the fail-closed outcome and requires operator recovery.
                return result;
            }
        }
    }
    if acquired.file_boundary {
        if let Err(blocker) = backend.rollback_file_boundary() {
            result.blockers.push(blocker);
        }
    }
    if acquired.loopback_exemption {
        if let Err(blocker) = backend.remove_loopback_exemption() {
            result.blockers.push(blocker);
        }
    }
    if acquired.wfp {
        if let Err(blocker) = backend.remove_wfp_policy() {
            result.blockers.push(blocker);
        }
    }
    if acquired.proxy {
        if let Err(blocker) = backend.stop_proxy() {
            result.blockers.push(blocker);
        }
    }
    if acquired.profile {
        if let Err(blocker) = backend.remove_lpac_profile() {
            result.blockers.push(blocker);
        }
    }
    if !result.blockers.is_empty() {
        // Cleanup is incomplete, so the maintenance lock remains held for
        // explicit operator recovery and no post-cleanup proof is issued.
        return result;
    }
    result.drain_proof = drain_proof.clone();
    match backend.finalize_cleanup(plan, drain_proof.as_ref()) {
        Ok(proof) => result.post_cleanup_proof = proof,
        Err(blocker) => result.blockers.push(blocker),
    }
    result
}

fn outcome_blocker(outcome: &ProcessOutcome, plan: &QualificationPlan) -> Option<Blocker> {
    if outcome.timed_out {
        Some(Blocker::new(
            Gate::OutputAndDeadline,
            "HARD_DEADLINE_EXCEEDED",
        ))
    } else if outcome.stdout_bytes > plan.stdout_limit {
        Some(Blocker::new(
            Gate::OutputAndDeadline,
            "STDOUT_LIMIT_EXCEEDED",
        ))
    } else if outcome.stderr_bytes > plan.stderr_limit {
        Some(Blocker::new(
            Gate::OutputAndDeadline,
            "STDERR_LIMIT_EXCEEDED",
        ))
    } else if outcome
        .stdout_artifact
        .as_ref()
        .is_some_and(|artifact| artifact.truncated)
    {
        Some(Blocker::new(
            Gate::OutputAndDeadline,
            "STDOUT_CAPTURE_TRUNCATED",
        ))
    } else if outcome
        .stderr_artifact
        .as_ref()
        .is_some_and(|artifact| artifact.truncated)
    {
        Some(Blocker::new(
            Gate::OutputAndDeadline,
            "STDERR_CAPTURE_TRUNCATED",
        ))
    } else if let Err(blocker) = validate_output_artifacts(outcome, plan) {
        Some(blocker)
    } else if let Err(blocker) = validate_capture_proof(outcome, plan) {
        Some(blocker)
    } else if outcome.exit_code != Some(0) {
        Some(Blocker::new(Gate::OutputAndDeadline, "CHILD_EXIT_NONZERO"))
    } else {
        None
    }
}

fn validate_capture_proof(
    outcome: &ProcessOutcome,
    plan: &QualificationPlan,
) -> Result<(), Blocker> {
    match outcome.sensitive_output_disposition {
        SensitiveOutputDisposition::NotScanned => {
            return Err(Blocker::new(
                Gate::OutputAndDeadline,
                "SENSITIVE_OUTPUT_SCAN_MISSING",
            ));
        }
        SensitiveOutputDisposition::SecretDetected => {
            return Err(Blocker::new(
                Gate::OutputAndDeadline,
                "SENSITIVE_OUTPUT_SECRET_DETECTED",
            ));
        }
        SensitiveOutputDisposition::Clean => {}
    }
    if outcome.network_or_auth_used != Some(true) {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "MODEL_NETWORK_AUTH_TRACE_NOT_CONFIRMED",
        ));
    }
    let (Some(stdout), Some(stderr)) = (
        outcome.stdout_artifact.as_ref(),
        outcome.stderr_artifact.as_ref(),
    ) else {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "OUTPUT_ARTIFACT_MISSING",
        ));
    };
    let expected_scan = sensitive_output_scan_attestation_sha256(
        &stdout.sha256,
        &stderr.sha256,
        &plan.proxy_token_sha256,
        outcome.sensitive_output_disposition,
    )?;
    if outcome.sensitive_output_scan_sha256 != expected_scan
        || !is_sha256_digest(&outcome.file_trace_sha256)
        || !is_sha256_digest(&outcome.process_trace_sha256)
    {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "CAPTURE_PROOF_INVALID",
        ));
    }
    Ok(())
}

fn validate_output_artifacts(
    outcome: &ProcessOutcome,
    plan: &QualificationPlan,
) -> Result<(), Blocker> {
    let stdout = outcome
        .stdout_artifact
        .as_ref()
        .ok_or_else(|| Blocker::new(Gate::OutputAndDeadline, "STDOUT_ARTIFACT_MISSING"))?;
    let stderr = outcome
        .stderr_artifact
        .as_ref()
        .ok_or_else(|| Blocker::new(Gate::OutputAndDeadline, "STDERR_ARTIFACT_MISSING"))?;
    if stdout.identity.path == stderr.identity.path
        || stdout.identity.file_id == stderr.identity.file_id
    {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "OUTPUT_ARTIFACT_ALIAS",
        ));
    }
    for (artifact, observed_length) in [
        (stdout, outcome.stdout_bytes),
        (stderr, outcome.stderr_bytes),
    ] {
        if !is_clean_absolute(&artifact.identity.path)
            || !artifact.identity.path.starts_with(&plan.runtime_root)
            || artifact.identity.creation_authority != FileCreationAuthority::NativeCreateNewOutput
            || artifact.identity.volume_serial != plan.evidence_identity.volume_serial
            || artifact.identity.file_id == [0; 16]
            || artifact.identity.parent_file_id == [0; 16]
            || artifact.identity.link_count != 1
            || artifact.identity.length != artifact.bytes.len() as u64
            || observed_length != artifact.bytes.len()
            || artifact.sha256 != sha256_digest(&artifact.bytes)?
            || artifact.identity.sha256 != artifact.sha256
        {
            return Err(Blocker::new(
                Gate::OutputAndDeadline,
                "OUTPUT_ARTIFACT_BINDING_MISMATCH",
            ));
        }
    }
    Ok(())
}

fn validate_prelaunch_observation(
    plan: &QualificationPlan,
    observation: &PrelaunchObservation,
) -> Result<(), Blocker> {
    let run_leaf = plan
        .runtime_root
        .file_name()
        .and_then(OsStr::to_str)
        .and_then(|value| value.strip_prefix("codex-t13b-"))
        .ok_or_else(|| Blocker::new(Gate::Manifest, "OS_PROFILE_RUN_ID_MISSING"))?;
    let expected_profile = format!("OpenAI.PKMWorkflow.Codex.T13b.{run_leaf}");
    let hashes = [
        &observation.wfp_intent_sha256,
        &observation.wfp_readback_sha256,
        &observation.wfp_negative_matrix_sha256,
        &observation.loopback_state_before_sha256,
        &observation.loopback_state_active_sha256,
        &observation.proxy_process_identity_sha256,
        &observation.parser_fixture_sha256,
        &observation.maintenance_lock_identity_sha256,
    ];
    let unique_filter_ids = observation
        .wfp_filter_ids
        .iter()
        .copied()
        .collect::<std::collections::BTreeSet<_>>();
    if observation.windows_major < 10
        || observation.windows_build == 0
        || observation.architecture != "amd64"
        || observation.appcontainer_profile_name != expected_profile
        || !observation.package_sid.starts_with("S-1-15-2-")
        || !observation.capability_sids.is_empty()
        || observation.same_sid_processes_before != 0
        || observation.wfp_filter_ids.len() != 3
        || unique_filter_ids.len() != observation.wfp_filter_ids.len()
        || unique_filter_ids.contains(&0)
        || hashes.iter().any(|value| !is_sha256_digest(value))
        || observation.wfp_intent_sha256 != observation.wfp_readback_sha256
        || observation.loopback_state_active_sha256 == observation.loopback_state_before_sha256
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "PRELAUNCH_OBSERVATION_INVALID",
        ));
    }
    Ok(())
}

fn validate_drain_proof(proof: &JobDrainProof) -> Result<(), Blocker> {
    if proof.descendant_count != 0
        || proof.same_sid_processes_after_job_drain != 0
        || !is_sha256_digest(&proof.job_accounting_sha256)
    {
        return Err(Blocker::new(Gate::JobDrained, "JOB_DRAIN_PROOF_INVALID"));
    }
    Ok(())
}

fn validate_post_cleanup_proof(
    prelaunch: &PrelaunchObservation,
    proof: &PostCleanupProof,
) -> Result<(), Blocker> {
    let hashes = [
        &proof.loopback_state_restored_sha256,
        &proof.wfp_state_restored_sha256,
        &proof.proxy_terminal_sha256,
        &proof.file_boundary_restored_sha256,
        &proof.appcontainer_profile_removed_sha256,
        &proof.maintenance_lock_released_sha256,
    ];
    if hashes.iter().any(|value| !is_sha256_digest(value))
        || proof.loopback_state_restored_sha256 != prelaunch.loopback_state_before_sha256
    {
        return Err(Blocker::new(
            Gate::CleanupFinalized,
            "POST_CLEANUP_PROOF_INVALID",
        ));
    }
    Ok(())
}

fn validate_qualification_evidence(
    plan: &QualificationPlan,
    prelaunch: &PrelaunchObservation,
    outcome: &ProcessOutcome,
    drain: &JobDrainProof,
    cleanup: &PostCleanupProof,
) -> Result<(), Blocker> {
    validate_prelaunch_observation(plan, prelaunch)?;
    validate_output_artifacts(outcome, plan)?;
    validate_capture_proof(outcome, plan)?;
    validate_drain_proof(drain)?;
    validate_post_cleanup_proof(prelaunch, cleanup)
}

pub fn os_qualification_closure_sha256(
    plan: &QualificationPlan,
    observation: &PrelaunchObservation,
    outcome: &ProcessOutcome,
    drain: &JobDrainProof,
    cleanup: &PostCleanupProof,
) -> Result<String, Blocker> {
    plan.validate()?;
    if let Some(blocker) = outcome_blocker(outcome, plan) {
        return Err(blocker);
    }
    validate_qualification_evidence(plan, observation, outcome, drain, cleanup)?;
    let mut closure = b"PKM-CODEX-T13B-OS-QUALIFICATION-CLOSURE-V2\0".to_vec();
    append_bytes(&mut closure, plan.intent_closure_sha256()?.as_bytes());
    for value in [
        observation.windows_major,
        observation.windows_minor,
        observation.windows_build,
        observation.windows_ubr,
    ] {
        append_bytes(&mut closure, &value.to_le_bytes());
    }
    append_bytes(&mut closure, observation.architecture.as_bytes());
    append_bytes(
        &mut closure,
        observation.appcontainer_profile_name.as_bytes(),
    );
    append_bytes(&mut closure, observation.package_sid.as_bytes());
    append_bytes(
        &mut closure,
        &(observation.capability_sids.len() as u64).to_le_bytes(),
    );
    for capability in &observation.capability_sids {
        append_bytes(&mut closure, capability.as_bytes());
    }
    append_bytes(
        &mut closure,
        &observation.same_sid_processes_before.to_le_bytes(),
    );
    append_bytes(
        &mut closure,
        &(observation.wfp_filter_ids.len() as u64).to_le_bytes(),
    );
    for filter_id in &observation.wfp_filter_ids {
        append_bytes(&mut closure, &filter_id.to_le_bytes());
    }
    for hash in [
        &observation.wfp_intent_sha256,
        &observation.wfp_readback_sha256,
        &observation.wfp_negative_matrix_sha256,
        &observation.loopback_state_before_sha256,
        &observation.loopback_state_active_sha256,
        &observation.proxy_process_identity_sha256,
        &observation.parser_fixture_sha256,
        &observation.maintenance_lock_identity_sha256,
    ] {
        append_bytes(&mut closure, hash.as_bytes());
    }
    append_bytes(
        &mut closure,
        &[match outcome.sensitive_output_disposition {
            SensitiveOutputDisposition::NotScanned => 0,
            SensitiveOutputDisposition::Clean => 1,
            SensitiveOutputDisposition::SecretDetected => 2,
        }],
    );
    for hash in [
        &outcome.sensitive_output_scan_sha256,
        &outcome.file_trace_sha256,
        &outcome.process_trace_sha256,
        &drain.job_accounting_sha256,
        &cleanup.loopback_state_restored_sha256,
        &cleanup.wfp_state_restored_sha256,
        &cleanup.proxy_terminal_sha256,
        &cleanup.file_boundary_restored_sha256,
        &cleanup.appcontainer_profile_removed_sha256,
        &cleanup.maintenance_lock_released_sha256,
    ] {
        append_bytes(&mut closure, hash.as_bytes());
    }
    append_bytes(&mut closure, &drain.descendant_count.to_le_bytes());
    append_bytes(
        &mut closure,
        &drain.same_sid_processes_after_job_drain.to_le_bytes(),
    );
    sha256_digest(&closure)
}

pub fn result_closure_sha256(
    plan: &QualificationPlan,
    observation: &PrelaunchObservation,
    outcome: &ProcessOutcome,
    drain: &JobDrainProof,
    cleanup: &PostCleanupProof,
) -> Result<String, Blocker> {
    plan.validate()?;
    if let Some(blocker) = outcome_blocker(outcome, plan) {
        return Err(blocker);
    }
    validate_qualification_evidence(plan, observation, outcome, drain, cleanup)?;
    let mut closure = b"PKM-CODEX-T13B-QUALIFICATION-RESULT-CLOSURE-V2\0".to_vec();
    append_bytes(
        &mut closure,
        os_qualification_closure_sha256(plan, observation, outcome, drain, cleanup)?.as_bytes(),
    );
    let (Some(stdout), Some(stderr)) = (
        outcome.stdout_artifact.as_ref(),
        outcome.stderr_artifact.as_ref(),
    ) else {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "OUTPUT_ARTIFACT_MISSING",
        ));
    };
    for artifact in [stdout, stderr] {
        append_file_identity(&mut closure, &artifact.identity)?;
        append_bytes(&mut closure, artifact.sha256.as_bytes());
        append_bytes(&mut closure, &(artifact.bytes.len() as u64).to_le_bytes());
        append_bytes(&mut closure, &[u8::from(artifact.truncated)]);
    }
    append_bytes(
        &mut closure,
        &outcome.exit_code.unwrap_or(u32::MAX).to_le_bytes(),
    );
    append_bytes(&mut closure, &[u8::from(outcome.timed_out)]);
    append_bytes(
        &mut closure,
        &[match outcome.network_or_auth_used {
            None => 0,
            Some(false) => 1,
            Some(true) => 2,
        }],
    );
    sha256_digest(&closure)
}

fn validate_paths(plan: &QualificationPlan) -> Result<(), Blocker> {
    let runtime_text = plan.runtime_root.to_str().unwrap_or_default();
    let run_id = runtime_text
        .strip_prefix(r"D:\personal\obsidian_workflow-runtime\scratch\codex-t13b-")
        .unwrap_or_default();
    if !is_clean_absolute(&plan.runtime_root)
        || !(16..=64).contains(&run_id.len())
        || !run_id.bytes().all(|value| value.is_ascii_hexdigit())
    {
        return Err(Blocker::new(
            Gate::Manifest,
            "RUNTIME_ROOT_NOT_ABSOLUTE_CLEAN",
        ));
    }
    for path in [
        &plan.binary_path,
        &plan.codex_home,
        &plan.run_root,
        &plan.evidence_path,
        &plan.schema_path,
        &plan.prompt_path,
    ] {
        if !is_clean_absolute(path) || !path.starts_with(&plan.runtime_root) {
            return Err(Blocker::new(Gate::Manifest, "PATH_OUTSIDE_RUNTIME"));
        }
    }
    if plan.codex_home == plan.run_root
        || plan.evidence_path == plan.schema_path
        || plan.evidence_path == plan.prompt_path
        || plan.schema_path == plan.prompt_path
    {
        return Err(Blocker::new(Gate::Manifest, "PATH_ALIAS"));
    }
    Ok(())
}

fn validate_environment(plan: &QualificationPlan) -> Result<(), Blocker> {
    if plan.environment.len() != EXPECTED_ENV_KEYS.len()
        || EXPECTED_ENV_KEYS
            .iter()
            .any(|key| !plan.environment.contains_key(*key))
    {
        return Err(Blocker::new(Gate::Manifest, "ENVIRONMENT_NOT_EXACT"));
    }
    if plan.environment.get("CODEX_HOME") != Some(&plan.codex_home.clone().into_os_string()) {
        return Err(Blocker::new(Gate::Manifest, "CODEX_HOME_MISMATCH"));
    }
    if plan.proxy_username != "pkm-t13b"
        || !is_sha256_digest(&plan.proxy_token_sha256)
        || plan.environment.get("NO_PROXY") != Some(&OsString::new())
        || plan.environment.get("TEMP") != Some(&plan.run_root.join("temp").into_os_string())
        || plan.environment.get("TMP") != Some(&plan.run_root.join("temp").into_os_string())
    {
        return Err(Blocker::new(Gate::Manifest, "ENVIRONMENT_VALUE_DRIFT"));
    }
    Ok(())
}

fn validate_proxy_control(plan: &QualificationPlan) -> Result<(), Blocker> {
    let authorities = plan
        .proxy_connect_allowlist
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>();
    let manifest = canonical_proxy_manifest_from_token_hash(
        plan.proxy_port,
        &authorities,
        &plan.proxy_token_sha256,
    )?;
    if plan.proxy_manifest_json != manifest.canonical_json
        || plan.proxy_manifest_sha256 != sha256_digest(&manifest.canonical_json)?
        || plan.proxy_manifest_hash != manifest.manifest_hash
        || plan.proxy_bind_endpoint_sha256 != manifest.bind_endpoint_sha256
        || plan.proxy_challenge == [0; 32]
        || plan.proxy_challenge_sha256 != sha256_digest(&plan.proxy_challenge)?
        || plan.proxy_process_id == 0
        || !is_sha256_digest(&plan.proxy_listener_identity_sha256)
        || !is_hmac_sha256_digest(&plan.proxy_readiness_hmac_sha256)
    {
        return Err(Blocker::new(
            Gate::Manifest,
            "PROXY_CONTROL_BINDING_MISMATCH",
        ));
    }
    let identity = format!(
        "{{\"bind_endpoint_sha256\":\"{}\",\"challenge_sha256\":\"{}\",\"listener_handle\":{},\"manifest_hash\":\"{}\",\"process_id\":{},\"schema_version\":\"t13b-proxy-readiness-v1\"}}",
        plan.proxy_bind_endpoint_sha256,
        plan.proxy_challenge_sha256,
        plan.proxy_listener_handle,
        plan.proxy_manifest_hash,
        plan.proxy_process_id,
    );
    if plan.proxy_listener_identity_sha256 != sha256_digest(identity.as_bytes())? {
        return Err(Blocker::new(
            Gate::Manifest,
            "PROXY_LISTENER_IDENTITY_MISMATCH",
        ));
    }
    let expected_control = format!(
        "{{\"bind_endpoint_sha256\":\"{}\",\"challenge_sha256\":\"{}\",\"listener_handle\":{},\"listener_identity_sha256\":\"{}\",\"manifest_hash\":\"{}\",\"process_id\":{},\"proof_hmac_sha256\":\"{}\",\"schema_version\":\"t13b-proxy-readiness-v1\"}}",
        plan.proxy_bind_endpoint_sha256,
        plan.proxy_challenge_sha256,
        plan.proxy_listener_handle,
        plan.proxy_listener_identity_sha256,
        plan.proxy_manifest_hash,
        plan.proxy_process_id,
        plan.proxy_readiness_hmac_sha256,
    )
    .into_bytes();
    let expected_audit = format!(
        "{{\"bind_endpoint_sha256\":\"{}\",\"listener_identity_sha256\":\"{}\",\"manifest_hash\":\"{}\",\"proof_hmac_sha256\":\"{}\",\"schema_version\":\"t13b-proxy-readiness-v1\"}}",
        plan.proxy_bind_endpoint_sha256,
        plan.proxy_listener_identity_sha256,
        plan.proxy_manifest_hash,
        plan.proxy_readiness_hmac_sha256,
    )
    .into_bytes();
    if plan.proxy_readiness_control_json != expected_control
        || plan.proxy_readiness_sha256 != sha256_digest(&expected_control)?
        || plan.proxy_readiness_audit_json != expected_audit
        || plan.proxy_readiness_audit_sha256 != sha256_digest(&expected_audit)?
    {
        return Err(Blocker::new(
            Gate::Manifest,
            "PROXY_READINESS_BINDING_MISMATCH",
        ));
    }
    Ok(())
}

fn proxy_token_digest_bytes(token: &[u8]) -> Result<String, Blocker> {
    let mut input = Vec::with_capacity(PROXY_TOKEN_DOMAIN.len() + token.len());
    input.extend_from_slice(PROXY_TOKEN_DOMAIN);
    input.extend_from_slice(token);
    let result = sha256_digest(&input);
    input.fill(0);
    result
}

fn valid_proxy_token(token: &[u8]) -> bool {
    (32..=128).contains(&token.len())
        && token
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(*byte, b'_' | b'-' | b'.' | b'~'))
}

pub fn sensitive_output_scan_attestation_sha256(
    stdout_sha256: &str,
    stderr_sha256: &str,
    proxy_token_sha256: &str,
    disposition: SensitiveOutputDisposition,
) -> Result<String, Blocker> {
    if !is_sha256_digest(stdout_sha256)
        || !is_sha256_digest(stderr_sha256)
        || !is_sha256_digest(proxy_token_sha256)
        || disposition == SensitiveOutputDisposition::NotScanned
    {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "SENSITIVE_OUTPUT_SCAN_ATTESTATION_INVALID",
        ));
    }
    let mut input = b"PKM-CODEX-T13B-SENSITIVE-OUTPUT-SCAN-V1\0".to_vec();
    append_bytes(&mut input, stdout_sha256.as_bytes());
    append_bytes(&mut input, stderr_sha256.as_bytes());
    append_bytes(&mut input, proxy_token_sha256.as_bytes());
    append_bytes(
        &mut input,
        &[match disposition {
            SensitiveOutputDisposition::NotScanned => 0,
            SensitiveOutputDisposition::Clean => 1,
            SensitiveOutputDisposition::SecretDetected => 2,
        }],
    );
    sha256_digest(&input)
}

/// Scans transient capture buffers before any output artifact is persisted.
///
/// The live launcher remains blocked until this is wired while the one-shot
/// token is still held. Callers must discard/zero secret-bearing buffers on a
/// positive result; a Debug redaction is not a substitute for this gate.
pub fn captured_output_contains_proxy_secret(
    stdout: &[u8],
    stderr: &[u8],
    username: &str,
    token: &[u8],
) -> bool {
    if username != "pkm-t13b" || !valid_proxy_token(token) {
        return true;
    }
    let mut userinfo = Vec::with_capacity(username.len() + token.len() + 2);
    userinfo.extend_from_slice(username.as_bytes());
    userinfo.push(b':');
    userinfo.extend_from_slice(token);
    let mut userinfo_at = userinfo.clone();
    userinfo_at.push(b'@');
    let mut basic = base64_encode(&userinfo);
    let mut basic_unpadded = basic.clone();
    while basic_unpadded.last() == Some(&b'=') {
        basic_unpadded.pop();
    }
    let mut basic_urlsafe = basic.clone();
    for byte in &mut basic_urlsafe {
        *byte = match *byte {
            b'+' => b'-',
            b'/' => b'_',
            value => value,
        };
    }
    let mut basic_urlsafe_unpadded = basic_urlsafe.clone();
    while basic_urlsafe_unpadded.last() == Some(&b'=') {
        basic_urlsafe_unpadded.pop();
    }
    let patterns: [&[u8]; 6] = [
        token,
        &userinfo,
        &userinfo_at,
        &basic,
        &basic_unpadded,
        &basic_urlsafe_unpadded,
    ];
    let mut found = 0u8;
    for haystack in [stdout, stderr] {
        let mut percent_once = percent_decode_once(haystack);
        let mut percent_twice = percent_decode_once(&percent_once);
        for candidate in [haystack, percent_once.as_slice(), percent_twice.as_slice()] {
            for needle in patterns {
                found |= u8::from(contains_subslice_full_scan(candidate, needle));
            }
            found |= u8::from(contains_subslice_full_scan(candidate, &basic_urlsafe));
        }
        percent_once.fill(0);
        percent_twice.fill(0);
    }
    userinfo.fill(0);
    userinfo_at.fill(0);
    basic.fill(0);
    basic_unpadded.fill(0);
    basic_urlsafe.fill(0);
    basic_urlsafe_unpadded.fill(0);
    found != 0
}

fn contains_subslice_full_scan(haystack: &[u8], needle: &[u8]) -> bool {
    if needle.is_empty() || haystack.len() < needle.len() {
        return false;
    }
    let mut any_equal = 0u8;
    for candidate in haystack.windows(needle.len()) {
        let difference = candidate
            .iter()
            .zip(needle)
            .fold(0u8, |value, (left, right)| value | (left ^ right));
        any_equal |= u8::from(difference == 0);
    }
    any_equal != 0
}

fn base64_encode(input: &[u8]) -> Vec<u8> {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = Vec::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let first = chunk[0];
        let second = chunk.get(1).copied().unwrap_or(0);
        let third = chunk.get(2).copied().unwrap_or(0);
        output.push(ALPHABET[(first >> 2) as usize]);
        output.push(ALPHABET[(((first & 0x03) << 4) | (second >> 4)) as usize]);
        output.push(if chunk.len() > 1 {
            ALPHABET[(((second & 0x0f) << 2) | (third >> 6)) as usize]
        } else {
            b'='
        });
        output.push(if chunk.len() > 2 {
            ALPHABET[(third & 0x3f) as usize]
        } else {
            b'='
        });
    }
    output
}

fn percent_decode_once(input: &[u8]) -> Vec<u8> {
    let mut output = Vec::with_capacity(input.len());
    let mut index = 0usize;
    while index < input.len() {
        if input[index] == b'%' && index + 2 < input.len() {
            if let (Some(high), Some(low)) =
                (hex_value(input[index + 1]), hex_value(input[index + 2]))
            {
                output.push((high << 4) | low);
                index += 3;
                continue;
            }
        }
        output.push(input[index]);
        index += 1;
    }
    output
}

const fn hex_value(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0u8, |difference, (left, right)| difference | (left ^ right))
        == 0
}

fn validate_argv(plan: &QualificationPlan) -> Result<(), Blocker> {
    if plan.model != plan.role.model()
        || plan.argv
            != frozen_codex_argv(
                plan.role,
                &plan.binary_path,
                &plan.schema_path,
                &plan.run_root,
            )
    {
        return Err(Blocker::new(Gate::Manifest, "ARGV_EXACT_CONTRACT_MISMATCH"));
    }
    for value in &plan.argv {
        let Some(value) = value.to_str() else {
            return Err(Blocker::new(Gate::Manifest, "ARGV_NOT_UTF8"));
        };
        if value.contains('\0')
            || value.eq_ignore_ascii_case("--dangerously-bypass-approvals-and-sandbox")
        {
            return Err(Blocker::new(Gate::Manifest, "ARGV_FORBIDDEN"));
        }
    }
    Ok(())
}

fn is_clean_absolute(path: &Path) -> bool {
    let text = path.to_str().unwrap_or_default();
    path.is_absolute()
        && !text.get(2..).unwrap_or_default().contains(':')
        && !path
            .components()
            .any(|component| matches!(component, Component::ParentDir | Component::CurDir))
}

fn validate_file_identities(plan: &QualificationPlan) -> Result<(), Blocker> {
    let identities = [
        (
            &plan.binary_identity,
            FileCreationAuthority::OfficialReleaseCopy,
        ),
        (
            &plan.evidence_identity,
            FileCreationAuthority::BrokerCreateNew,
        ),
        (
            &plan.schema_identity,
            FileCreationAuthority::BrokerCreateNew,
        ),
        (
            &plan.prompt_identity,
            FileCreationAuthority::BrokerCreateNew,
        ),
    ];
    let expected_paths = [
        &plan.binary_path,
        &plan.evidence_path,
        &plan.schema_path,
        &plan.prompt_path,
    ];
    for ((identity, authority), path) in identities.iter().zip(expected_paths) {
        if &identity.path != path
            || identity.creation_authority != *authority
            || identity.volume_serial == 0
            || identity.file_id == [0; 16]
            || identity.parent_file_id == [0; 16]
            || identity.length == 0
            || identity.link_count != 1
            || !is_sha256_digest(&identity.sha256)
        {
            return Err(Blocker::new(Gate::Manifest, "NTFS_IDENTITY_INVALID"));
        }
    }
    if plan.binary_identity.sha256 != plan.binary_sha256 {
        return Err(Blocker::new(
            Gate::Manifest,
            "BINARY_IDENTITY_HASH_MISMATCH",
        ));
    }
    let volume = plan.binary_identity.volume_serial;
    if identities
        .iter()
        .any(|(identity, _)| identity.volume_serial != volume)
    {
        return Err(Blocker::new(Gate::Manifest, "NTFS_VOLUME_MISMATCH"));
    }
    for (index, (left, _)) in identities.iter().enumerate() {
        if identities[index + 1..]
            .iter()
            .any(|(right, _)| left.file_id == right.file_id)
        {
            return Err(Blocker::new(Gate::Manifest, "NTFS_FILE_ID_ALIAS"));
        }
    }
    Ok(())
}

fn append_file_identity(output: &mut Vec<u8>, identity: &NtfsFileIdentity) -> Result<(), Blocker> {
    append_os(output, identity.path.as_os_str())?;
    append_bytes(output, &identity.volume_serial.to_le_bytes());
    append_bytes(output, &identity.file_id);
    append_bytes(output, &identity.parent_file_id);
    append_bytes(output, &identity.length.to_le_bytes());
    append_bytes(output, identity.sha256.as_bytes());
    append_bytes(output, &identity.link_count.to_le_bytes());
    append_bytes(output, identity.creation_authority.as_str().as_bytes());
    Ok(())
}

fn append_os(output: &mut Vec<u8>, value: &OsStr) -> Result<(), Blocker> {
    let value = value
        .to_str()
        .ok_or_else(|| Blocker::new(Gate::Manifest, "MANIFEST_VALUE_NOT_UTF8"))?;
    append_bytes(output, value.as_bytes());
    Ok(())
}

fn append_bytes(output: &mut Vec<u8>, value: &[u8]) {
    output.extend_from_slice(&(value.len() as u64).to_le_bytes());
    output.extend_from_slice(value);
}

fn nonempty(value: &str) -> Option<String> {
    if value.is_empty() {
        None
    } else {
        Some(value.to_owned())
    }
}

fn is_sha256_digest(value: &str) -> bool {
    value.len() == 71
        && value.starts_with("sha256:")
        && value[7..]
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn is_hmac_sha256_digest(value: &str) -> bool {
    value.len() == 76
        && value.starts_with("hmac-sha256:")
        && value[12..]
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn sha256_digest(bytes: &[u8]) -> Result<String, Blocker> {
    let length = u32::try_from(bytes.len())
        .map_err(|_| Blocker::new(Gate::Manifest, "MANIFEST_TOO_LARGE"))?;
    let mut algorithm = std::ptr::null_mut();
    // SAFETY: the output pointer is valid and the algorithm identifier is a
    // process-lifetime static UTF-16 string supplied by windows-sys.
    let status = unsafe {
        BCryptOpenAlgorithmProvider(&mut algorithm, BCRYPT_SHA256_ALGORITHM, std::ptr::null(), 0)
    };
    if status < 0 || algorithm.is_null() {
        return Err(Blocker::new(Gate::Manifest, "CNG_SHA256_UNAVAILABLE"));
    }
    let mut digest = [0u8; 32];
    // SAFETY: all buffers remain valid for the duration of this synchronous
    // call, and their exact lengths are supplied.
    let hash_status = unsafe {
        BCryptHash(
            algorithm,
            std::ptr::null(),
            0,
            bytes.as_ptr(),
            length,
            digest.as_mut_ptr(),
            digest.len() as u32,
        )
    };
    // SAFETY: algorithm was returned by BCryptOpenAlgorithmProvider and is
    // closed exactly once after the hash attempt.
    let close_status = unsafe { BCryptCloseAlgorithmProvider(algorithm, 0) };
    if hash_status < 0 || close_status < 0 {
        return Err(Blocker::new(Gate::Manifest, "CNG_SHA256_FAILED"));
    }
    let mut text = String::with_capacity(71);
    text.push_str("sha256:");
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut text, "{byte:02x}")
            .map_err(|_| Blocker::new(Gate::Manifest, "HASH_FORMAT_FAILED"))?;
    }
    Ok(text)
}

fn hmac_sha256_digest(key: &[u8], message: &[u8]) -> Result<String, Blocker> {
    let key_length = u32::try_from(key.len())
        .map_err(|_| Blocker::new(Gate::ProxyAttestation, "PROXY_HMAC_KEY_TOO_LARGE"))?;
    let message_length = u32::try_from(message.len())
        .map_err(|_| Blocker::new(Gate::ProxyAttestation, "PROXY_HMAC_INPUT_TOO_LARGE"))?;
    let mut algorithm = std::ptr::null_mut();
    // SAFETY: output storage and static algorithm identifier are valid. The
    // HMAC flag makes the BCryptHash secret parameter a keyed HMAC key.
    let status = unsafe {
        BCryptOpenAlgorithmProvider(
            &mut algorithm,
            BCRYPT_SHA256_ALGORITHM,
            std::ptr::null(),
            BCRYPT_ALG_HANDLE_HMAC_FLAG,
        )
    };
    if status < 0 || algorithm.is_null() {
        return Err(Blocker::new(
            Gate::ProxyAttestation,
            "CNG_HMAC_SHA256_UNAVAILABLE",
        ));
    }
    let mut digest = [0u8; 32];
    // SAFETY: all key/message/output buffers remain live for this synchronous
    // one-shot HMAC call and their exact lengths are supplied.
    let hmac_status = unsafe {
        BCryptHash(
            algorithm,
            key.as_ptr(),
            key_length,
            message.as_ptr(),
            message_length,
            digest.as_mut_ptr(),
            digest.len() as u32,
        )
    };
    // SAFETY: algorithm is owned by this function and closed exactly once.
    let close_status = unsafe { BCryptCloseAlgorithmProvider(algorithm, 0) };
    if hmac_status < 0 || close_status < 0 {
        return Err(Blocker::new(
            Gate::ProxyAttestation,
            "CNG_HMAC_SHA256_FAILED",
        ));
    }
    let mut text = String::with_capacity(76);
    text.push_str("hmac-sha256:");
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut text, "{byte:02x}")
            .map_err(|_| Blocker::new(Gate::ProxyAttestation, "HASH_FORMAT_FAILED"))?;
    }
    Ok(text)
}
