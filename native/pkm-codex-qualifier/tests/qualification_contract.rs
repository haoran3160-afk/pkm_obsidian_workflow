use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Duration;

use pkm_codex_qualifier::{
    canonical_proxy_manifest, canonical_proxy_readiness, captured_output_contains_proxy_secret,
    fixed_launch_attributes, fixed_wfp_policy, frozen_codex_argv, os_qualification_closure_sha256,
    sensitive_output_scan_attestation_sha256, verify_proxy_readiness, AddressFamily, Blocker,
    CapturedOutputArtifact, CodexRole, FileCreationAuthority, Gate, IsolationBackend,
    JobDrainProof, NetworkAction, NtfsFileIdentity, PostCleanupProof, PrelaunchObservation,
    ProcessOutcome, QualificationEngine, QualificationPlan, SensitiveOutputDisposition, Terminal,
    TransportProtocol, EXPECTED_CODEX_EXE_SHA256, EXPECTED_CODEX_VERSION, FROZEN_CONFIG_OVERRIDES,
    GOLDEN_PROXY_PORT,
};

const PROXY_TOKEN: &[u8] = b"CANARY_proxy_token_0123456789abcdef_ABCDEF";

const PYTHON_FROZEN_CONFIG_OVERRIDES: &[&str] = &[
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
const GOLDEN_READINESS_CONTROL: &[u8] = r#"{"bind_endpoint_sha256":"sha256:af4873d2ce02ac6018b9b5b21d868f12e293635af76f82173c23e34cb32ffc48","challenge_sha256":"sha256:4113d54b0b611294b7f595b691c9db541fc0fc719848d6c5c34522eacc0b3a24","listener_handle":31337,"listener_identity_sha256":"sha256:5eb18d795c7530ca3edb4cd1f7b9120aaeb78f9798d7dcf19e00acb2a537c352","manifest_hash":"sha256:d31aa2078742d704be2dacfdae1056d9af8b254842375d7827e0108c9dba71a0","process_id":4242,"proof_hmac_sha256":"hmac-sha256:24dec4f786acbf2393c47b08b1a6c1012511105afea081e07acff392e68d2e54","schema_version":"t13b-proxy-readiness-v1"}"#.as_bytes();

#[derive(Debug, Default)]
struct RecordingBackend {
    fail_at: Vec<Gate>,
    events: Vec<Gate>,
    outcome: ProcessOutcome,
    prelaunch_observation: Option<PrelaunchObservation>,
    drain_proof: Option<JobDrainProof>,
}

impl RecordingBackend {
    fn failing(gate: Gate) -> Self {
        Self {
            fail_at: vec![gate],
            ..Self::default()
        }
    }

    fn failing_many(gates: &[Gate]) -> Self {
        Self {
            fail_at: gates.to_vec(),
            ..Self::default()
        }
    }

    fn record(&mut self, gate: Gate) -> Result<(), Blocker> {
        self.events.push(gate);
        if self.fail_at.contains(&gate) {
            Err(Blocker::new(gate, "INJECTED_GATE_FAILURE"))
        } else {
            Ok(())
        }
    }
}

impl IsolationBackend for RecordingBackend {
    fn require_elevation(&mut self) -> Result<(), Blocker> {
        self.record(Gate::Elevation)
    }

    fn verify_binary(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        self.record(Gate::BinaryIdentity)
    }

    fn prepare_lpac_profile(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        self.record(Gate::AppContainerLpac)
    }

    fn install_loopback_exemption(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        self.record(Gate::LoopbackExemption)
    }

    fn attest_proxy(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        self.record(Gate::ProxyAttestation)
    }

    fn install_wfp_policy(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        self.record(Gate::WfpTransaction)
    }

    fn verify_file_boundary(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        self.record(Gate::FileBoundary)
    }

    fn prepare_job(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        self.record(Gate::JobPrepared)
    }

    fn observe_prelaunch(
        &mut self,
        plan: &QualificationPlan,
    ) -> Result<PrelaunchObservation, Blocker> {
        self.record(Gate::PrelaunchObserved)?;
        Ok(self
            .prelaunch_observation
            .take()
            .unwrap_or_else(|| harness_prelaunch_observation(plan)))
    }

    fn create_suspended(&mut self, _plan: &QualificationPlan) -> Result<(), Blocker> {
        self.record(Gate::ChildCreatedSuspended)
    }

    fn resume(&mut self) -> Result<(), Blocker> {
        self.record(Gate::ChildResumed)
    }

    fn wait_and_capture(&mut self, plan: &QualificationPlan) -> Result<ProcessOutcome, Blocker> {
        self.record(Gate::OutputAndDeadline)?;
        let mut outcome = std::mem::take(&mut self.outcome);
        outcome
            .stdout_artifact
            .get_or_insert_with(|| harness_output_artifact(plan, "stdout.jsonl", 0x77));
        outcome
            .stderr_artifact
            .get_or_insert_with(|| harness_output_artifact(plan, "stderr.log", 0x88));
        let digest = |value: char| format!("sha256:{}", value.to_string().repeat(64));
        if outcome.sensitive_output_disposition == SensitiveOutputDisposition::NotScanned {
            outcome.sensitive_output_disposition = SensitiveOutputDisposition::Clean;
        }
        if outcome.network_or_auth_used == Some(false) {
            outcome.network_or_auth_used = Some(true);
        }
        if outcome.file_trace_sha256.is_empty() {
            outcome.file_trace_sha256 = digest('1');
        }
        if outcome.process_trace_sha256.is_empty() {
            outcome.process_trace_sha256 = digest('2');
        }
        if outcome.sensitive_output_scan_sha256.is_empty() {
            outcome.sensitive_output_scan_sha256 = sensitive_output_scan_attestation_sha256(
                &outcome.stdout_artifact.as_ref().unwrap().sha256,
                &outcome.stderr_artifact.as_ref().unwrap().sha256,
                &plan.proxy_token_sha256,
                outcome.sensitive_output_disposition,
            )?;
        }
        Ok(outcome)
    }

    fn terminate_job(&mut self) -> Result<(), Blocker> {
        self.record(Gate::JobTerminated)
    }

    fn drain_job(&mut self) -> Result<JobDrainProof, Blocker> {
        self.record(Gate::JobDrained)?;
        Ok(self.drain_proof.take().unwrap_or_else(harness_drain_proof))
    }

    fn rollback_file_boundary(&mut self) -> Result<(), Blocker> {
        self.record(Gate::FileBoundaryRestored)
    }

    fn remove_loopback_exemption(&mut self) -> Result<(), Blocker> {
        self.record(Gate::LoopbackExemptionRemoved)
    }

    fn remove_wfp_policy(&mut self) -> Result<(), Blocker> {
        self.record(Gate::WfpPolicyRemoved)
    }

    fn stop_proxy(&mut self) -> Result<(), Blocker> {
        self.record(Gate::ProxyStopped)
    }

    fn remove_lpac_profile(&mut self) -> Result<(), Blocker> {
        self.record(Gate::AppContainerRemoved)
    }

    fn finalize_cleanup(
        &mut self,
        _plan: &QualificationPlan,
        drain: Option<&JobDrainProof>,
    ) -> Result<Option<PostCleanupProof>, Blocker> {
        self.record(Gate::CleanupFinalized)?;
        Ok(drain.map(|_| harness_post_cleanup_proof()))
    }
}

fn harness_prelaunch_observation(plan: &QualificationPlan) -> PrelaunchObservation {
    let run_id = plan
        .runtime_root
        .file_name()
        .and_then(|value| value.to_str())
        .and_then(|value| value.strip_prefix("codex-t13b-"))
        .expect("test runtime id");
    let digest = |value: char| format!("sha256:{}", value.to_string().repeat(64));
    PrelaunchObservation {
        windows_major: 10,
        windows_minor: 0,
        windows_build: 26_100,
        windows_ubr: 4_350,
        architecture: "amd64".into(),
        appcontainer_profile_name: format!("OpenAI.PKMWorkflow.Codex.T13b.{run_id}"),
        package_sid: "S-1-15-2-123456789".into(),
        capability_sids: Vec::new(),
        same_sid_processes_before: 0,
        wfp_filter_ids: vec![11, 12, 13],
        wfp_intent_sha256: digest('a'),
        wfp_readback_sha256: digest('a'),
        wfp_negative_matrix_sha256: digest('b'),
        loopback_state_before_sha256: digest('c'),
        loopback_state_active_sha256: digest('d'),
        proxy_process_identity_sha256: digest('e'),
        parser_fixture_sha256: digest('f'),
        maintenance_lock_identity_sha256: digest('3'),
    }
}

fn harness_drain_proof() -> JobDrainProof {
    JobDrainProof {
        descendant_count: 0,
        same_sid_processes_after_job_drain: 0,
        job_accounting_sha256: format!("sha256:{}", "5".repeat(64)),
    }
}

fn harness_post_cleanup_proof() -> PostCleanupProof {
    let digest = |value: char| format!("sha256:{}", value.to_string().repeat(64));
    PostCleanupProof {
        loopback_state_restored_sha256: digest('c'),
        wfp_state_restored_sha256: digest('6'),
        proxy_terminal_sha256: digest('7'),
        file_boundary_restored_sha256: digest('8'),
        appcontainer_profile_removed_sha256: digest('9'),
        maintenance_lock_released_sha256: digest('0'),
    }
}

fn harness_output_artifact(
    plan: &QualificationPlan,
    leaf: &str,
    file_id_byte: u8,
) -> CapturedOutputArtifact {
    const EMPTY_SHA256: &str =
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    CapturedOutputArtifact {
        identity: NtfsFileIdentity {
            path: plan.run_root.join("output").join(leaf),
            volume_serial: plan.evidence_identity.volume_serial,
            file_id: [file_id_byte; 16],
            parent_file_id: [0x99; 16],
            length: 0,
            sha256: EMPTY_SHA256.into(),
            link_count: 1,
            creation_authority: FileCreationAuthority::NativeCreateNewOutput,
        },
        bytes: Vec::new(),
        sha256: EMPTY_SHA256.into(),
        truncated: false,
    }
}

fn harness_process_outcome(plan: &QualificationPlan) -> ProcessOutcome {
    let stdout = harness_output_artifact(plan, "stdout.jsonl", 0x77);
    let stderr = harness_output_artifact(plan, "stderr.log", 0x88);
    let scan = sensitive_output_scan_attestation_sha256(
        &stdout.sha256,
        &stderr.sha256,
        &plan.proxy_token_sha256,
        SensitiveOutputDisposition::Clean,
    )
    .unwrap();
    ProcessOutcome {
        stdout_artifact: Some(stdout),
        stderr_artifact: Some(stderr),
        network_or_auth_used: Some(true),
        sensitive_output_disposition: SensitiveOutputDisposition::Clean,
        sensitive_output_scan_sha256: scan,
        file_trace_sha256: format!("sha256:{}", "1".repeat(64)),
        process_trace_sha256: format!("sha256:{}", "2".repeat(64)),
        ..ProcessOutcome::default()
    }
}

fn valid_plan() -> QualificationPlan {
    let root =
        PathBuf::from(r"D:\personal\obsidian_workflow-runtime\scratch\codex-t13b-0123456789abcdef");
    let mut environment = BTreeMap::new();
    environment.insert(
        "CODEX_HOME".into(),
        root.join("codex-home").into_os_string(),
    );
    environment.insert("NO_PROXY".into(), "".into());
    environment.insert(
        "TEMP".into(),
        root.join("run").join("temp").into_os_string(),
    );
    environment.insert("TMP".into(), root.join("run").join("temp").into_os_string());

    let identity = |path: PathBuf, hash: &str, authority| NtfsFileIdentity {
        path,
        volume_serial: 0x1122_3344_5566_7788,
        file_id: [hash.as_bytes()[7]; 16],
        parent_file_id: [0x22; 16],
        length: 128,
        sha256: hash.into(),
        link_count: 1,
        creation_authority: authority,
    };
    let binary_path = root.join("release/codex-x86_64-pc-windows-msvc.exe");
    let evidence_path = root.join("sealed/evidence.json");
    let schema_path = root.join("sealed/schema.json");
    let prompt_path = root.join("sealed/prompt.txt");
    let mut plan = QualificationPlan {
        binary_path: binary_path.clone(),
        binary_sha256: EXPECTED_CODEX_EXE_SHA256.into(),
        expected_version: EXPECTED_CODEX_VERSION.into(),
        runtime_root: root.clone(),
        codex_home: root.join("codex-home"),
        run_root: root.join("run"),
        evidence_path: evidence_path.clone(),
        schema_path: schema_path.clone(),
        prompt_path: prompt_path.clone(),
        binary_identity: identity(
            binary_path,
            EXPECTED_CODEX_EXE_SHA256,
            FileCreationAuthority::OfficialReleaseCopy,
        ),
        evidence_identity: identity(
            evidence_path,
            "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            FileCreationAuthority::BrokerCreateNew,
        ),
        schema_identity: identity(
            schema_path,
            "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            FileCreationAuthority::BrokerCreateNew,
        ),
        prompt_identity: identity(
            prompt_path,
            "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
            FileCreationAuthority::BrokerCreateNew,
        ),
        stdin_bytes: Vec::new(),
        stdin_sha256: String::new(),
        stdin_length: 0,
        manifest_bytes: Vec::new(),
        manifest_sha256: String::new(),
        role: CodexRole::Generator,
        model: "gpt-5.6-terra".into(),
        argv: frozen_codex_argv(
            CodexRole::Generator,
            &root.join("release/codex-x86_64-pc-windows-msvc.exe"),
            &root.join("sealed/schema.json"),
            &root.join("run"),
        ),
        environment,
        proxy_username: String::new(),
        proxy_token_sha256: String::new(),
        proxy_port: GOLDEN_PROXY_PORT,
        proxy_connect_allowlist: Vec::new(),
        proxy_manifest_json: Vec::new(),
        proxy_manifest_sha256: String::new(),
        proxy_manifest_hash: String::new(),
        proxy_bind_endpoint_sha256: String::new(),
        proxy_readiness_control_json: Vec::new(),
        proxy_readiness_sha256: String::new(),
        proxy_readiness_audit_json: Vec::new(),
        proxy_readiness_audit_sha256: String::new(),
        proxy_listener_identity_sha256: String::new(),
        proxy_readiness_hmac_sha256: String::new(),
        proxy_challenge: [0; 32],
        proxy_challenge_sha256: String::new(),
        proxy_process_id: 0,
        proxy_listener_handle: 0,
        deadline: Duration::from_secs(180),
        stdout_limit: 1_048_576,
        stderr_limit: 262_144,
    };
    plan.bind_proxy_secret_hash("pkm-t13b", PROXY_TOKEN)
        .expect("bind proxy canary");
    plan.bind_proxy_control(
        &["chatgpt.com:443"],
        PROXY_TOKEN,
        [b'C'; 32],
        4_242,
        31_337,
        GOLDEN_READINESS_CONTROL,
    )
    .expect("bind proxy control proof");
    plan.bind_stdin(
        b"frozen prompt\n\n<SEALED_INPUT_JSON>\n{\"fixture\":true}\n<END_SEALED_INPUT>\n",
    )
    .expect("bind stdin");
    plan.seal_manifest().expect("seal test manifest");
    plan
}

#[test]
fn manifest_binds_ntfs_identity_and_complete_stdin_not_only_paths() {
    assert_eq!(
        valid_plan().stdin_length as usize,
        valid_plan().stdin_bytes.len()
    );
    assert!(valid_plan().stdin_sha256.starts_with("sha256:"));

    let mut cases = Vec::new();
    let mut hardlinked = valid_plan();
    hardlinked.evidence_identity.link_count = 2;
    cases.push(hardlinked);
    let mut changed_file_id = valid_plan();
    changed_file_id.schema_identity.file_id[0] ^= 1;
    cases.push(changed_file_id);
    let mut changed_stdin = valid_plan();
    changed_stdin.stdin_bytes.push(b'!');
    cases.push(changed_stdin);
    let mut ads = valid_plan();
    ads.prompt_path = PathBuf::from(
        r"D:\personal\obsidian_workflow-runtime\scratch\codex-t13b-0123456789abcdef\sealed\prompt.txt:secret",
    );
    ads.prompt_identity.path = ads.prompt_path.clone();
    cases.push(ads);

    for case in cases {
        let mut backend = RecordingBackend::default();
        let report = QualificationEngine::qualify(&case, &mut backend);
        assert_eq!(report.terminal, Terminal::BlockedPrequalification);
        assert_eq!(report.blocker.unwrap().gate, Gate::Manifest);
        assert!(backend.events.is_empty());
    }
}

#[test]
fn argv_is_an_exact_role_model_schema_and_config_contract() {
    let plan = valid_plan();
    assert_eq!(plan.argv[0], plan.binary_path.as_os_str());
    assert!(plan.argv.iter().any(|value| value == "gpt-5.6-terra"));
    assert!(!plan.argv.iter().any(|value| value == "gpt-5.6-sol"));
    assert_eq!(plan.argv.last().unwrap(), "-");

    let mut mutations = Vec::new();
    let mut extra = valid_plan();
    extra.argv.insert(1, "--verbose".into());
    mutations.push(extra);
    let mut duplicate_model = valid_plan();
    duplicate_model
        .argv
        .extend(["--model".into(), "gpt-5.6-sol".into()]);
    mutations.push(duplicate_model);
    let mut wrong_role = valid_plan();
    wrong_role.role = CodexRole::Reviewer;
    wrong_role.model = "gpt-5.6-sol".into();
    mutations.push(wrong_role);

    for mutation in mutations {
        let mut backend = RecordingBackend::default();
        let report = QualificationEngine::qualify(&mutation, &mut backend);
        assert_eq!(report.blocker.unwrap().gate, Gate::Manifest);
        assert!(backend.events.is_empty());
    }
}

#[test]
fn proxy_secret_is_domain_bound_but_never_serialized_or_reported() {
    let secret = "CANARY_proxy_token_0123456789abcdef_ABCDEF";
    let plan = valid_plan();
    assert!(!String::from_utf8_lossy(&plan.manifest_bytes).contains(secret));
    assert!(!plan.manifest_sha256.contains(secret));
    assert!(!plan.intent_closure_sha256().unwrap().contains(secret));

    let mut backend = RecordingBackend::failing(Gate::Elevation);
    let report = QualificationEngine::qualify(&plan, &mut backend);
    assert!(!format!("{report:?}").contains(secret));

    let mut tampered = plan;
    tampered.environment.insert(
        "HTTPS_PROXY".into(),
        format!("http://pkm-t13b:DIFFERENT_token_0123456789abcdef@127.0.0.1:{GOLDEN_PROXY_PORT}")
            .into(),
    );
    let mut backend = RecordingBackend::default();
    let report = QualificationEngine::qualify(&tampered, &mut backend);
    assert_eq!(report.blocker.unwrap().gate, Gate::Manifest);
    assert!(backend.events.is_empty());
}

#[test]
fn transient_output_secret_scan_detects_raw_userinfo_basic_and_percent_forms() {
    const BASIC: &[u8] = b"cGttLXQxM2I6Q0FOQVJZX3Byb3h5X3Rva2VuXzAxMjM0NTY3ODlhYmNkZWZfQUJDREVG";
    let percent = PROXY_TOKEN
        .iter()
        .map(|byte| format!("%{byte:02X}"))
        .collect::<String>();
    let token_text = std::str::from_utf8(PROXY_TOKEN).unwrap();
    let partial_percent = format!("%43{}", &token_text[1..]);
    let mixed_case_percent = token_text.replacen('_', "%5f", 1);
    let double_percent = format!("%2543{}", &token_text[1..]);
    for leak in [
        PROXY_TOKEN.to_vec(),
        [b"pkm-t13b:".as_slice(), PROXY_TOKEN, b"@127.0.0.1"].concat(),
        BASIC.to_vec(),
        percent.into_bytes(),
        partial_percent.into_bytes(),
        mixed_case_percent.into_bytes(),
        double_percent.into_bytes(),
    ] {
        assert!(captured_output_contains_proxy_secret(
            b"clean stdout",
            &leak,
            "pkm-t13b",
            PROXY_TOKEN,
        ));
    }
    assert!(!captured_output_contains_proxy_secret(
        b"clean stdout",
        b"ordinary stderr",
        "pkm-t13b",
        PROXY_TOKEN,
    ));
}

#[test]
fn plan_rejects_identity_environment_and_path_drift_before_backend_calls() {
    let mut cases = Vec::new();

    let mut wrong_hash = valid_plan();
    wrong_hash.binary_sha256 = format!("sha256:{}", "0".repeat(64));
    cases.push(wrong_hash);

    let mut wrong_version = valid_plan();
    wrong_version.expected_version = "codex-cli 0.125.0".into();
    cases.push(wrong_version);

    let mut inherited_path = valid_plan();
    inherited_path
        .environment
        .insert("PATH".into(), r"C:\Windows".into());
    cases.push(inherited_path);

    let mut escaped_evidence = valid_plan();
    escaped_evidence.evidence_path = PathBuf::from(r"D:\personal\ObsidianVault\secret.md");
    cases.push(escaped_evidence);

    let mut wrong_proxy = valid_plan();
    wrong_proxy.proxy_port += 1;
    cases.push(wrong_proxy);

    for plan in cases {
        let mut backend = RecordingBackend::default();
        let report = QualificationEngine::qualify(&plan, &mut backend);
        assert_eq!(report.terminal, Terminal::BlockedPrequalification);
        assert_eq!(report.blocker.as_ref().unwrap().gate, Gate::Manifest);
        assert!(backend.events.is_empty());
        assert!(!report.secret_materialized_in_child);
    }
}

#[test]
fn every_pre_child_gate_failure_creates_and_resumes_no_child() {
    let pre_child = [
        Gate::Elevation,
        Gate::BinaryIdentity,
        Gate::AppContainerLpac,
        Gate::ProxyAttestation,
        Gate::WfpTransaction,
        Gate::LoopbackExemption,
        Gate::FileBoundary,
        Gate::JobPrepared,
        Gate::PrelaunchObserved,
    ];

    for failed_gate in pre_child {
        let mut backend = RecordingBackend::failing(failed_gate);
        let report = QualificationEngine::qualify(&valid_plan(), &mut backend);
        assert_eq!(report.terminal, Terminal::BlockedPrequalification);
        assert!(!backend.events.contains(&Gate::ChildCreatedSuspended));
        assert!(!backend.events.contains(&Gate::ChildResumed));
        assert_eq!(
            backend.events.contains(&Gate::JobTerminated),
            matches!(failed_gate, Gate::JobPrepared | Gate::PrelaunchObserved),
        );
        assert!(!report.secret_materialized_in_child);
    }
}

#[test]
fn invalid_prelaunch_os_readback_is_rejected_before_secret_or_child_creation() {
    let plan = valid_plan();
    let mut observation = harness_prelaunch_observation(&plan);
    observation.capability_sids.push("S-1-15-3-1".into());
    let mut backend = RecordingBackend {
        prelaunch_observation: Some(observation),
        ..RecordingBackend::default()
    };
    let report = QualificationEngine::qualify(&plan, &mut backend);

    assert_eq!(report.terminal, Terminal::BlockedPrequalification);
    assert_eq!(report.blocker.unwrap().gate, Gate::PrelaunchObserved);
    assert!(!backend.events.contains(&Gate::ChildCreatedSuspended));
    assert!(!backend.events.contains(&Gate::ChildResumed));
    assert!(!report.secret_materialized_in_child);
    assert!(report.result_closure_sha256.is_none());
}

#[test]
fn acquired_global_state_is_rolled_back_immediately_in_reverse_order() {
    let cases = [
        (Gate::AppContainerLpac, vec![Gate::AppContainerRemoved]),
        (
            Gate::ProxyAttestation,
            vec![Gate::ProxyStopped, Gate::AppContainerRemoved],
        ),
        (
            Gate::WfpTransaction,
            vec![
                Gate::WfpPolicyRemoved,
                Gate::ProxyStopped,
                Gate::AppContainerRemoved,
            ],
        ),
        (
            Gate::LoopbackExemption,
            vec![
                Gate::LoopbackExemptionRemoved,
                Gate::WfpPolicyRemoved,
                Gate::ProxyStopped,
                Gate::AppContainerRemoved,
            ],
        ),
        (
            Gate::FileBoundary,
            vec![
                Gate::FileBoundaryRestored,
                Gate::LoopbackExemptionRemoved,
                Gate::WfpPolicyRemoved,
                Gate::ProxyStopped,
                Gate::AppContainerRemoved,
            ],
        ),
        (
            Gate::JobPrepared,
            vec![
                Gate::JobTerminated,
                Gate::JobDrained,
                Gate::FileBoundaryRestored,
                Gate::LoopbackExemptionRemoved,
                Gate::WfpPolicyRemoved,
                Gate::ProxyStopped,
                Gate::AppContainerRemoved,
            ],
        ),
    ];

    for (failed_gate, expected_cleanup) in cases {
        let mut backend = RecordingBackend::failing(failed_gate);
        let report = QualificationEngine::qualify(&valid_plan(), &mut backend);
        assert_eq!(report.blocker.unwrap().gate, failed_gate);
        let cleanup = backend
            .events
            .into_iter()
            .filter(|gate| {
                matches!(
                    gate,
                    Gate::JobTerminated
                        | Gate::JobDrained
                        | Gate::FileBoundaryRestored
                        | Gate::LoopbackExemptionRemoved
                        | Gate::WfpPolicyRemoved
                        | Gate::ProxyStopped
                        | Gate::AppContainerRemoved
                )
            })
            .collect::<Vec<_>>();
        assert_eq!(cleanup, expected_cleanup);
    }
}

#[test]
fn cleanup_failure_is_primary_and_preserves_the_trigger_as_secondary() {
    let mut backend = RecordingBackend::failing_many(&[
        Gate::FileBoundary,
        Gate::WfpPolicyRemoved,
        Gate::AppContainerRemoved,
    ]);
    let report = QualificationEngine::qualify(&valid_plan(), &mut backend);

    assert_eq!(
        report.blocker,
        Some(Blocker::new(
            Gate::WfpPolicyRemoved,
            "INJECTED_GATE_FAILURE",
        ))
    );
    assert_eq!(
        report.secondary_blocker,
        Some(Blocker::new(Gate::FileBoundary, "INJECTED_GATE_FAILURE"))
    );
    assert_eq!(
        report.cleanup_blockers,
        vec![
            Blocker::new(Gate::WfpPolicyRemoved, "INJECTED_GATE_FAILURE"),
            Blocker::new(Gate::AppContainerRemoved, "INJECTED_GATE_FAILURE"),
        ]
    );
}

#[test]
fn child_is_created_suspended_only_after_wfp_file_and_job_gates() {
    let mut backend = RecordingBackend::default();
    let report = QualificationEngine::qualify(&valid_plan(), &mut backend);

    assert_eq!(
        report.terminal,
        Terminal::ContractHarnessCompletedUnqualified
    );
    assert_eq!(
        backend.events,
        vec![
            Gate::Elevation,
            Gate::BinaryIdentity,
            Gate::AppContainerLpac,
            Gate::ProxyAttestation,
            Gate::WfpTransaction,
            Gate::LoopbackExemption,
            Gate::FileBoundary,
            Gate::JobPrepared,
            Gate::PrelaunchObserved,
            Gate::ChildCreatedSuspended,
            Gate::ChildResumed,
            Gate::OutputAndDeadline,
            Gate::JobDrained,
            Gate::FileBoundaryRestored,
            Gate::LoopbackExemptionRemoved,
            Gate::WfpPolicyRemoved,
            Gate::ProxyStopped,
            Gate::AppContainerRemoved,
            Gate::CleanupFinalized,
        ]
    );
    assert!(report.secret_materialized_in_child);
    assert!(report.result_closure_sha256.is_some());
    assert!(report.outcome.as_ref().unwrap().stdout_artifact.is_some());
}

#[test]
fn truncated_output_is_retained_for_hash_only_audit_but_never_completes() {
    let plan = valid_plan();
    let mut stdout = harness_output_artifact(&plan, "stdout.jsonl", 0x77);
    stdout.truncated = true;
    let mut backend = RecordingBackend {
        outcome: ProcessOutcome {
            stdout_artifact: Some(stdout),
            stderr_artifact: Some(harness_output_artifact(&plan, "stderr.log", 0x88)),
            ..ProcessOutcome::default()
        },
        ..RecordingBackend::default()
    };

    let report = QualificationEngine::qualify(&plan, &mut backend);
    assert_eq!(report.terminal, Terminal::BlockedPrequalification);
    assert_eq!(
        report.blocker,
        Some(Blocker::new(
            Gate::OutputAndDeadline,
            "STDOUT_CAPTURE_TRUNCATED",
        ))
    );
    assert!(report.outcome.is_some());
    assert!(report.result_closure_sha256.is_none());
    assert!(report.os_qualification_closure_sha256.is_none());
}

#[test]
fn output_artifact_bytes_are_bound_but_redacted_from_debug_reports() {
    let plan = valid_plan();
    let mut stdout = harness_output_artifact(&plan, "stdout.jsonl", 0x77);
    stdout.bytes = b"CANARY_model_output_must_not_enter_debug".to_vec();
    stdout.identity.length = stdout.bytes.len() as u64;
    // Deliberately leave the empty digest: the engine must reject a caller's
    // self-reported identity rather than accept the byte count alone.
    let mut backend = RecordingBackend {
        outcome: ProcessOutcome {
            stdout_bytes: stdout.bytes.len(),
            stdout_artifact: Some(stdout),
            stderr_artifact: Some(harness_output_artifact(&plan, "stderr.log", 0x88)),
            ..ProcessOutcome::default()
        },
        ..RecordingBackend::default()
    };

    let report = QualificationEngine::qualify(&plan, &mut backend);
    assert_eq!(
        report.blocker,
        Some(Blocker::new(
            Gate::OutputAndDeadline,
            "OUTPUT_ARTIFACT_BINDING_MISMATCH",
        ))
    );
    assert!(!format!("{report:?}").contains("CANARY_model_output"));
}

#[test]
fn os_observed_closure_binds_build_sid_zero_capabilities_wfp_and_traces() {
    let plan = valid_plan();
    let observation = harness_prelaunch_observation(&plan);
    let outcome = harness_process_outcome(&plan);
    let drain = harness_drain_proof();
    let cleanup = harness_post_cleanup_proof();
    let closure = os_qualification_closure_sha256(&plan, &observation, &outcome, &drain, &cleanup)
        .expect("valid post-cleanup OS-observed closure");

    let mut changed_build = harness_prelaunch_observation(&plan);
    changed_build.windows_build += 1;
    assert_ne!(
        closure,
        os_qualification_closure_sha256(
            &plan,
            &changed_build,
            &harness_process_outcome(&plan),
            &harness_drain_proof(),
            &harness_post_cleanup_proof(),
        )
        .expect("different build remains a valid distinct observation"),
    );

    let mut broad_capability = harness_prelaunch_observation(&plan);
    broad_capability.capability_sids.push("S-1-15-3-1".into());
    assert!(os_qualification_closure_sha256(
        &plan,
        &broad_capability,
        &harness_process_outcome(&plan),
        &harness_drain_proof(),
        &harness_post_cleanup_proof(),
    )
    .is_err());

    let mut shallow_wfp_readback = harness_prelaunch_observation(&plan);
    shallow_wfp_readback.wfp_readback_sha256 = format!("sha256:{}", "9".repeat(64));
    assert!(os_qualification_closure_sha256(
        &plan,
        &shallow_wfp_readback,
        &harness_process_outcome(&plan),
        &harness_drain_proof(),
        &harness_post_cleanup_proof(),
    )
    .is_err());

    let mut stale_loopback_state = harness_post_cleanup_proof();
    stale_loopback_state.loopback_state_restored_sha256 = format!("sha256:{}", "8".repeat(64));
    assert!(os_qualification_closure_sha256(
        &plan,
        &harness_prelaunch_observation(&plan),
        &harness_process_outcome(&plan),
        &harness_drain_proof(),
        &stale_loopback_state,
    )
    .is_err());

    for failed_capture in [
        ProcessOutcome {
            exit_code: Some(10),
            ..harness_process_outcome(&plan)
        },
        ProcessOutcome {
            sensitive_output_disposition: SensitiveOutputDisposition::SecretDetected,
            ..harness_process_outcome(&plan)
        },
        ProcessOutcome {
            network_or_auth_used: None,
            ..harness_process_outcome(&plan)
        },
        ProcessOutcome {
            sensitive_output_scan_sha256: format!("sha256:{}", "4".repeat(64)),
            ..harness_process_outcome(&plan)
        },
    ] {
        assert!(os_qualification_closure_sha256(
            &plan,
            &harness_prelaunch_observation(&plan),
            &failed_capture,
            &harness_drain_proof(),
            &harness_post_cleanup_proof(),
        )
        .is_err());
    }
}

#[test]
fn resume_or_capture_failure_terminates_and_drains_the_job() {
    for failed_gate in [
        Gate::ChildCreatedSuspended,
        Gate::ChildResumed,
        Gate::OutputAndDeadline,
    ] {
        let mut backend = RecordingBackend::failing(failed_gate);
        let report = QualificationEngine::qualify(&valid_plan(), &mut backend);

        assert_eq!(report.terminal, Terminal::BlockedPrequalification);
        assert!(backend.events.contains(&Gate::JobTerminated));
        assert!(backend.events.contains(&Gate::JobDrained));
        assert!(report.secret_materialized_in_child);
        assert!(report.child_created);
        assert_eq!(
            report.child_resumed,
            failed_gate != Gate::ChildCreatedSuspended
        );
        assert_eq!(
            report.network_or_auth_used,
            if matches!(failed_gate, Gate::ChildResumed | Gate::OutputAndDeadline) {
                None
            } else {
                Some(false)
            },
        );
    }
}

#[test]
fn timeout_output_overflow_or_nonzero_exit_fail_closed() {
    let outcomes = [
        ProcessOutcome {
            timed_out: true,
            ..ProcessOutcome::default()
        },
        ProcessOutcome {
            stdout_bytes: 1_048_577,
            ..ProcessOutcome::default()
        },
        ProcessOutcome {
            stderr_bytes: 262_145,
            ..ProcessOutcome::default()
        },
        ProcessOutcome {
            exit_code: Some(10),
            ..ProcessOutcome::default()
        },
    ];

    for outcome in outcomes {
        let mut backend = RecordingBackend {
            outcome,
            ..RecordingBackend::default()
        };
        let report = QualificationEngine::qualify(&valid_plan(), &mut backend);
        assert_eq!(report.terminal, Terminal::BlockedPrequalification);
        assert!(backend.events.contains(&Gate::JobTerminated));
        assert!(backend.events.contains(&Gate::JobDrained));
    }
}

#[test]
fn caller_cannot_claim_completion_before_a_zero_descendant_drain_proof() {
    let mut invalid = harness_drain_proof();
    invalid.descendant_count = 1;
    let mut backend = RecordingBackend {
        drain_proof: Some(invalid),
        ..RecordingBackend::default()
    };
    let report = QualificationEngine::qualify(&valid_plan(), &mut backend);

    assert_eq!(report.terminal, Terminal::BlockedPrequalification);
    assert_eq!(
        report.blocker,
        Some(Blocker::new(Gate::JobDrained, "JOB_DRAIN_PROOF_INVALID"))
    );
    assert_eq!(
        report.failed_drain_proof.as_ref().unwrap().descendant_count,
        1
    );
    assert_eq!(report.drain_proof.as_ref().unwrap().descendant_count, 0);
    assert!(report.post_cleanup_proof.is_some());
    assert!(report.result_closure_sha256.is_none());
    assert!(report.os_qualification_closure_sha256.is_none());
}

#[test]
fn invalid_drain_proof_on_failure_preserves_network_acl_profile_and_lock() {
    let mut invalid = harness_drain_proof();
    invalid.same_sid_processes_after_job_drain = 1;
    let mut backend = RecordingBackend {
        fail_at: vec![Gate::OutputAndDeadline],
        drain_proof: Some(invalid),
        ..RecordingBackend::default()
    };
    let report = QualificationEngine::qualify(&valid_plan(), &mut backend);

    assert_eq!(report.terminal, Terminal::BlockedPrequalification);
    assert_eq!(report.blocker.unwrap().code, "JOB_DRAIN_PROOF_INVALID");
    assert_eq!(
        report
            .failed_drain_proof
            .as_ref()
            .unwrap()
            .same_sid_processes_after_job_drain,
        1
    );
    assert!(report.drain_proof.is_none());
    assert!(report.post_cleanup_proof.is_none());
    assert!(report.result_closure_sha256.is_none());
    assert!(report.os_qualification_closure_sha256.is_none());
    assert!(!backend.events.contains(&Gate::FileBoundaryRestored));
    assert!(!backend.events.contains(&Gate::LoopbackExemptionRemoved));
    assert!(!backend.events.contains(&Gate::WfpPolicyRemoved));
    assert!(!backend.events.contains(&Gate::ProxyStopped));
    assert!(!backend.events.contains(&Gate::AppContainerRemoved));
    assert!(!backend.events.contains(&Gate::CleanupFinalized));
}

#[test]
fn failure_to_drain_can_never_be_reported_as_qualified() {
    let mut backend = RecordingBackend::failing(Gate::JobDrained);
    let report = QualificationEngine::qualify(&valid_plan(), &mut backend);

    assert_eq!(report.terminal, Terminal::BlockedPrequalification);
    assert_eq!(report.blocker.unwrap().gate, Gate::JobDrained);
    assert!(backend.events.contains(&Gate::JobTerminated));
    assert!(!backend.events.contains(&Gate::FileBoundaryRestored));
    assert!(!backend.events.contains(&Gate::LoopbackExemptionRemoved));
    assert!(!backend.events.contains(&Gate::WfpPolicyRemoved));
    assert!(!backend.events.contains(&Gate::AppContainerRemoved));
}

#[test]
fn failed_job_termination_preserves_every_restrictive_boundary_and_lock() {
    let mut backend =
        RecordingBackend::failing_many(&[Gate::OutputAndDeadline, Gate::JobTerminated]);
    let report = QualificationEngine::qualify(&valid_plan(), &mut backend);

    assert_eq!(report.terminal, Terminal::BlockedPrequalification);
    assert_eq!(report.blocker.unwrap().gate, Gate::JobTerminated);
    assert!(!backend.events.contains(&Gate::JobDrained));
    assert!(!backend.events.contains(&Gate::FileBoundaryRestored));
    assert!(!backend.events.contains(&Gate::LoopbackExemptionRemoved));
    assert!(!backend.events.contains(&Gate::WfpPolicyRemoved));
    assert!(!backend.events.contains(&Gate::AppContainerRemoved));
    assert!(!backend.events.contains(&Gate::CleanupFinalized));
}

#[test]
fn wfp_contract_has_one_narrow_permit_and_package_bound_v4_v6_default_deny() {
    let rules = fixed_wfp_policy(GOLDEN_PROXY_PORT).expect("valid prebound proxy port");
    let permits: Vec<_> = rules
        .iter()
        .filter(|rule| rule.action == NetworkAction::Permit)
        .collect();
    assert_eq!(permits.len(), 1);
    assert_eq!(permits[0].family, AddressFamily::Ipv4);
    assert_eq!(permits[0].remote_address.as_deref(), Some("127.0.0.1"));
    assert_eq!(permits[0].remote_port, Some(GOLDEN_PROXY_PORT));
    assert_eq!(permits[0].protocol, Some(TransportProtocol::Tcp));
    assert!(permits[0].package_sid_required);

    for family in [AddressFamily::Ipv4, AddressFamily::Ipv6] {
        let block = rules
            .iter()
            .find(|rule| rule.family == family && rule.action == NetworkAction::Block)
            .expect("explicit family default deny");
        assert!(block.package_sid_required);
        assert_eq!(block.remote_address, None);
        assert_eq!(block.remote_port, None);
        assert_eq!(block.protocol, None);
    }
}

#[test]
fn rust_config_and_argv_are_byte_order_identical_to_the_frozen_python_contract() {
    assert_eq!(FROZEN_CONFIG_OVERRIDES, PYTHON_FROZEN_CONFIG_OVERRIDES);
    let plan = valid_plan();
    let expected = frozen_codex_argv(
        plan.role,
        &plan.binary_path,
        &plan.schema_path,
        &plan.run_root,
    );
    assert_eq!(plan.argv, expected);
    let config_values = plan
        .argv
        .windows(2)
        .filter_map(|pair| (pair[0] == "-c").then_some(pair[1].to_string_lossy().into_owned()))
        .collect::<Vec<_>>();
    assert_eq!(
        config_values,
        PYTHON_FROZEN_CONFIG_OVERRIDES
            .iter()
            .map(|value| (*value).to_owned())
            .collect::<Vec<_>>()
    );
}

#[test]
fn launch_attributes_are_suspended_lpac_job_bound_and_child_restricted() {
    let attributes = fixed_launch_attributes();
    assert!(attributes.create_suspended);
    assert!(attributes.security_capabilities);
    assert!(attributes.all_application_packages_opt_out);
    assert!(attributes.restrict_child_processes);
    assert!(attributes.atomic_job_list);
    assert!(attributes.explicit_handle_allowlist);
    assert!(!attributes.breakaway_allowed);
}

#[test]
fn rust_proxy_protocol_matches_the_frozen_python_golden_bytes() {
    const TOKEN: &[u8] = b"CANARY_proxy_token_0123456789abcdef_ABCDEF";
    const GOLDEN_MANIFEST: &[u8] = r#"{"authorization_mode":"BASIC_USERINFO_ENV_ONLY","authorization_username_sha256":"sha256:5f27b95a2a488a37d08dd32e7ea7977a39d791e29aa7911b1a74038f6f076827","bind_host":"127.0.0.1","bind_port":43117,"connect_allowlist":["chatgpt.com:443"],"deadline_milliseconds":900000,"listener_mode":"BROKER_PREBOUND_INHERITED","manifest_hash":"sha256:d31aa2078742d704be2dacfdae1056d9af8b254842375d7827e0108c9dba71a0","max_client_hello_bytes":32768,"max_connections":1,"max_header_bytes":8192,"max_tunnel_bytes":1048576,"schema_version":"t13b-loopback-connect-v1","token_sha256":"sha256:52174e6d68471b6768d4d0d88a552bb083b49d341462d47ee0adc434ea9d5271"}"#.as_bytes();
    const GOLDEN_CONTROL: &[u8] = r#"{"bind_endpoint_sha256":"sha256:af4873d2ce02ac6018b9b5b21d868f12e293635af76f82173c23e34cb32ffc48","challenge_sha256":"sha256:4113d54b0b611294b7f595b691c9db541fc0fc719848d6c5c34522eacc0b3a24","listener_handle":31337,"listener_identity_sha256":"sha256:5eb18d795c7530ca3edb4cd1f7b9120aaeb78f9798d7dcf19e00acb2a537c352","manifest_hash":"sha256:d31aa2078742d704be2dacfdae1056d9af8b254842375d7827e0108c9dba71a0","process_id":4242,"proof_hmac_sha256":"hmac-sha256:24dec4f786acbf2393c47b08b1a6c1012511105afea081e07acff392e68d2e54","schema_version":"t13b-proxy-readiness-v1"}"#.as_bytes();
    const GOLDEN_AUDIT: &[u8] = r#"{"bind_endpoint_sha256":"sha256:af4873d2ce02ac6018b9b5b21d868f12e293635af76f82173c23e34cb32ffc48","listener_identity_sha256":"sha256:5eb18d795c7530ca3edb4cd1f7b9120aaeb78f9798d7dcf19e00acb2a537c352","manifest_hash":"sha256:d31aa2078742d704be2dacfdae1056d9af8b254842375d7827e0108c9dba71a0","proof_hmac_sha256":"hmac-sha256:24dec4f786acbf2393c47b08b1a6c1012511105afea081e07acff392e68d2e54","schema_version":"t13b-proxy-readiness-v1"}"#.as_bytes();

    let manifest = canonical_proxy_manifest(GOLDEN_PROXY_PORT, &["chatgpt.com:443"], TOKEN)
        .expect("derive canonical manifest");
    assert_eq!(manifest.canonical_json, GOLDEN_MANIFEST);
    assert!(!manifest
        .canonical_json
        .windows(TOKEN.len())
        .any(|part| part == TOKEN));

    let readiness =
        verify_proxy_readiness(&manifest, TOKEN, &[b'C'; 32], 4_242, 31_337, GOLDEN_CONTROL)
            .expect("verify canonical readiness");
    assert_eq!(readiness.canonical_control_json, GOLDEN_CONTROL);
    assert_eq!(readiness.canonical_audit_json, GOLDEN_AUDIT);

    let mut forged = GOLDEN_CONTROL.to_vec();
    *forged.last_mut().expect("nonempty fixture") = b'!';
    assert_eq!(
        verify_proxy_readiness(&manifest, TOKEN, &[b'C'; 32], 4_242, 31_337, &forged),
        Err(Blocker::new(
            Gate::ProxyAttestation,
            "PROXY_READINESS_PROOF_MISMATCH",
        ))
    );
}

#[test]
fn rust_generated_proxy_vector_is_frozen_for_python_reverse_verification() {
    const TOKEN: &[u8] = b"CANARY_rust_proxy_token_0123456789abcdef_XYZ";
    const EXPECTED_MANIFEST: &[u8] = br#"{"authorization_mode":"BASIC_USERINFO_ENV_ONLY","authorization_username_sha256":"sha256:5f27b95a2a488a37d08dd32e7ea7977a39d791e29aa7911b1a74038f6f076827","bind_host":"127.0.0.1","bind_port":54219,"connect_allowlist":["api.openai.com:443"],"deadline_milliseconds":900000,"listener_mode":"BROKER_PREBOUND_INHERITED","manifest_hash":"sha256:f5bd26b178dacd6fa37c0e91d77e86876307af93cac6c9ba62864210777ca3eb","max_client_hello_bytes":32768,"max_connections":1,"max_header_bytes":8192,"max_tunnel_bytes":1048576,"schema_version":"t13b-loopback-connect-v1","token_sha256":"sha256:b53f58bd02c2fdd86bb8ccc4e05ab30496b60ca6631779b1680b78fd1e3c8d49"}"#;
    const EXPECTED_CONTROL: &[u8] = br#"{"bind_endpoint_sha256":"sha256:f6b98659566ba56ad11f251cef702d2caed3ac0a460964b04b7a736d27db7b4d","challenge_sha256":"sha256:16b72cfab7dbca73cb348f4e59a74b5c56d6e95574c1e9ca84850d69f5fa9430","listener_handle":424242,"listener_identity_sha256":"sha256:d6362b72ea3085c5c24b8f88821dda08be564a96c7c47b79be94db6fd24ca801","manifest_hash":"sha256:f5bd26b178dacd6fa37c0e91d77e86876307af93cac6c9ba62864210777ca3eb","process_id":7331,"proof_hmac_sha256":"hmac-sha256:4fb52539cd1b0cc948b32c8e196d5cbdcd1e60d6a20c7d608b36b6b62724f456","schema_version":"t13b-proxy-readiness-v1"}"#;
    const EXPECTED_AUDIT: &[u8] = br#"{"bind_endpoint_sha256":"sha256:f6b98659566ba56ad11f251cef702d2caed3ac0a460964b04b7a736d27db7b4d","listener_identity_sha256":"sha256:d6362b72ea3085c5c24b8f88821dda08be564a96c7c47b79be94db6fd24ca801","manifest_hash":"sha256:f5bd26b178dacd6fa37c0e91d77e86876307af93cac6c9ba62864210777ca3eb","proof_hmac_sha256":"hmac-sha256:4fb52539cd1b0cc948b32c8e196d5cbdcd1e60d6a20c7d608b36b6b62724f456","schema_version":"t13b-proxy-readiness-v1"}"#;
    let manifest = canonical_proxy_manifest(54_219, &["api.openai.com:443"], TOKEN)
        .expect("Rust manifest vector");
    let readiness = canonical_proxy_readiness(&manifest, TOKEN, &[b'R'; 32], 7_331, 424_242)
        .expect("Rust readiness vector");

    assert_eq!(manifest.canonical_json, EXPECTED_MANIFEST);
    assert_eq!(readiness.canonical_control_json, EXPECTED_CONTROL);
    assert_eq!(readiness.canonical_audit_json, EXPECTED_AUDIT);
    assert!(!manifest
        .canonical_json
        .windows(TOKEN.len())
        .any(|part| part == TOKEN));
}
