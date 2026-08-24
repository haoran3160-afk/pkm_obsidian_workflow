use std::fmt::Write as _;
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use std::process::Command;
use std::sync::Mutex;

use windows_sys::Win32::Security::Cryptography::{
    BCryptCloseAlgorithmProvider, BCryptHash, BCryptOpenAlgorithmProvider, BCRYPT_SHA256_ALGORITHM,
};
use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeleteAppContainerProfile,
};
use windows_sys::Win32::Security::{FreeSid, IsValidSid};

use pkm_codex_qualifier::{
    native_helper_diagnostic_contract, native_preflight_contract,
    native_preflight_diagnostic_contract, native_static_blockers, process_is_elevated,
    run_native_helper_diagnostic, run_native_preflight_diagnostic, Blocker, Gate, IsolationBackend,
    NativeWindowsBackend, EXPECTED_CODEX_EXE_SHA256, EXPECTED_CODEX_VERSION,
};

static NATIVE_CHILD_TEST_LOCK: Mutex<()> = Mutex::new(());

fn file_sha256(path: &Path) -> String {
    let bytes = std::fs::read(path).expect("read artifact for independent SHA-256");
    let mut algorithm = std::ptr::null_mut();
    // SAFETY: output storage and static algorithm identifier are valid.
    assert!(
        unsafe {
            BCryptOpenAlgorithmProvider(
                &mut algorithm,
                BCRYPT_SHA256_ALGORITHM,
                std::ptr::null(),
                0,
            )
        } >= 0
    );
    assert!(!algorithm.is_null());
    let mut digest = [0u8; 32];
    // SAFETY: all input/output buffers and lengths are exact and live.
    let status = unsafe {
        BCryptHash(
            algorithm,
            std::ptr::null(),
            0,
            bytes.as_ptr(),
            bytes.len().try_into().expect("bounded artifact length"),
            digest.as_mut_ptr(),
            digest.len() as u32,
        )
    };
    // SAFETY: algorithm is uniquely owned and closed exactly once.
    assert!(unsafe { BCryptCloseAlgorithmProvider(algorithm, 0) } >= 0);
    assert!(status >= 0);
    let mut encoded = String::with_capacity(64);
    for byte in digest {
        write!(&mut encoded, "{byte:02x}").expect("write SHA-256 hex");
    }
    format!("sha256:{encoded}")
}

fn json_string_values(document: &str, key: &str) -> Vec<String> {
    let marker = format!("\"{key}\":\"");
    let mut remaining = document;
    let mut values = Vec::new();
    while let Some(start) = remaining.find(&marker) {
        let value_start = start + marker.len();
        let tail = &remaining[value_start..];
        let Some(end) = tail.find('"') else {
            break;
        };
        values.push(tail[..end].replace("\\\\", "\\"));
        remaining = &tail[end + 1..];
    }
    values
}

fn wide_null(value: &std::ffi::OsStr) -> Vec<u16> {
    value.encode_wide().chain(std::iter::once(0)).collect()
}

#[test]
fn native_preflight_contract_is_closed_no_auth_no_network() {
    let contract = native_preflight_contract();

    assert_eq!(
        contract.binary_path.to_string_lossy(),
        r"D:\personal\obsidian_workflow-runtime\durable\codex\0.147.0-prequal-20260818-01\expanded\codex-x86_64-pc-windows-msvc.exe"
    );
    assert_eq!(contract.binary_length, 298_668_336);
    assert_eq!(contract.binary_sha256, EXPECTED_CODEX_EXE_SHA256);
    assert_eq!(contract.argv, ["--version"]);
    assert_eq!(
        contract.expected_stdout,
        format!("{EXPECTED_CODEX_VERSION}\n")
    );
    assert_eq!(contract.expected_stderr, "");
    assert_eq!(contract.deadline_seconds, 30);
    assert_eq!(contract.mode, "PREFLIGHT_ONLY");
    assert_eq!(contract.console_creation_mode, "DETACHED_PROCESS");
    assert!(!contract.live);
    assert!(!contract.auth_materialized);
    assert!(!contract.network_requested);
    assert!(!contract.auth_isolation);
    assert!(!contract.network_enforcement);
    assert_eq!(
        contract.fixed_environment_keys,
        [
            "ALL_PROXY",
            "CODEX_HOME",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
        ]
    );
}

#[test]
fn native_diagnostic_contract_has_exactly_four_non_configurable_variants() {
    let contract = native_preflight_diagnostic_contract();

    assert_eq!(contract.command, "diagnose-preflight");
    assert_eq!(contract.terminal, "BLOCKED_DIAGNOSTIC");
    assert!(!contract.production);
    assert!(!contract.live);
    assert!(!contract.auth_materialized);
    assert!(!contract.network_requested);
    assert_eq!(
        contract.variants,
        [
            ("A", true, true, false),
            ("B", true, false, false),
            ("C", false, true, true),
            ("D", false, false, true),
        ]
    );
}

#[test]
fn helper_diagnostic_contract_is_fixed_to_two_controls_and_two_detached_probes() {
    let contract = native_helper_diagnostic_contract();

    assert_eq!(contract.command, "diagnose-helper");
    assert_eq!(contract.terminal, "BLOCKED_HELPER_DIAGNOSTIC");
    assert!(!contract.production);
    assert!(!contract.live);
    assert!(!contract.auth_materialized);
    assert!(!contract.network_requested);
    assert_eq!(
        contract.variants,
        [
            ("B", true, false, false, "CREATE_NO_WINDOW"),
            ("D", false, false, true, "CREATE_NO_WINDOW"),
            ("E", true, false, false, "DETACHED_PROCESS"),
            ("F", true, true, false, "DETACHED_PROCESS"),
        ]
    );
    assert!(contract.debug_process);
    assert!(contract.job_completion_port);
    assert!(contract.codex_home_change_trace);
    assert!(contract.final_codex_home_inventory);
    assert_eq!(contract.external_file_access_trace, "ETW_REQUIRED_NOT_RUN");
}

#[test]
fn unresolved_native_proofs_are_explicit_not_self_reported_booleans() {
    let codes: Vec<_> = native_static_blockers()
        .into_iter()
        .map(|blocker| blocker.code)
        .collect();
    assert_eq!(
        codes,
        vec![
            "CLOSED_NATIVE_PLAN_BUILDER_NOT_READY",
            "RUNTIME_ROOT_CREATE_NEW_AUTHORITY_NOT_READY",
            "WINDOWS_BUILD_QUALIFICATION_NOT_BOUND",
            "CODEX_0147_BINARY_QUALIFICATION_NOT_RUN",
            "CODEX_0147_DETACHED_PREFLIGHT_NOT_RELEASE_BOUND",
            "CHILD_PROCESS_RESTRICTION_NEGATIVE_CANARY_NOT_RELEASE_BOUND",
            "PREFLIGHT_FULL_TOKEN_AUTH_ISOLATION_NOT_QUALIFIED",
            "PREFLIGHT_NETWORK_ENFORCEMENT_NOT_QUALIFIED",
            "APPCONTAINER_PROFILE_DIAGNOSTIC_NOT_RELEASE_BOUND",
            "APPCONTAINER_PROFILE_CRASH_RECOVERY_NOT_QUALIFIED",
            "LPAC_TOKEN_DIAGNOSTIC_NOT_RELEASE_BOUND",
            "ZERO_NETWORK_CAPABILITY_LOOPBACK_SMOKE_NOT_QUALIFIED",
            "PROXY_PROCESS_IDENTITY_NOT_QUALIFIED",
            "PROXY_EXECUTION_NOT_IMPLEMENTED",
            "PROXY_SECRET_ONESHOT_CHILD_ENV_NOT_READY",
            "CODEX_0147_PROXY_AUTH_TRACE_REQUIRED",
            "SUBSCRIPTION_AUDIENCE_TRACE_REQUIRED",
            "WFP_ELEVATED_READBACK_AND_NEGATIVE_MATRIX_REQUIRED",
            "LOOPBACK_EXEMPTION_NOT_QUALIFIED",
            "ANCESTOR_HANDLE_CHAIN_NOT_QUALIFIED",
            "AUTH_CACHE_ACL_HANDLE_NOT_QUALIFIED",
            "FILE_ACCESS_ETW_TRACE_REQUIRED",
            "MAINTENANCE_LOCK_LIFECYCLE_NOT_QUALIFIED",
            "CREATE_SUSPENDED_LPAC_LAUNCH_NOT_READY",
            "CREATE_RESUME_RAII_TRANSACTION_NOT_READY",
            "STDIN_PIPE_BOUNDED_CAPTURE_NOT_READY",
            "CHILD_ENVIRONMENT_TRACE_NOT_QUALIFIED",
            "TRACE_ZERO_TOOL_SUBPROCESS_NOT_INTEGRATED",
            "OUTPUT_ARTIFACT_ATTESTATION_NOT_READY",
            "OUTPUT_SECRET_LEAK_SCAN_NOT_READY",
            "OS_OBSERVED_CLOSURE_NOT_PRODUCED",
            "JOB_WFP_TEARDOWN_NOT_QUALIFIED",
            "POST_CLEANUP_OBSERVATION_NOT_READY",
        ]
    );
}

#[test]
fn elevation_probe_and_native_gate_agree_without_creating_child_state() {
    let elevated = process_is_elevated().expect("query current token elevation");
    let mut backend = NativeWindowsBackend::new();
    let result = backend.require_elevation();

    if elevated {
        assert_eq!(result, Ok(()));
    } else {
        assert_eq!(
            result,
            Err(Blocker::new(Gate::Elevation, "ELEVATION_REQUIRED"))
        );
    }
    assert!(!backend.child_created());
    assert!(!backend.child_resumed());
    assert!(!backend.secret_materialized_in_child());
}

#[test]
fn real_doctor_is_always_fail_closed_and_never_accepts_free_form_paths() {
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let output = Command::new(executable)
        .arg("doctor")
        .output()
        .expect("run native doctor");
    assert_eq!(output.status.code(), Some(3));
    let stdout = String::from_utf8(output.stdout).expect("doctor UTF-8");
    assert!(stdout.contains("\"terminal\":\"BLOCKED_PREQUALIFICATION\""));
    assert!(stdout.contains("SUBSCRIPTION_AUDIENCE_TRACE_REQUIRED"));
    if !process_is_elevated().unwrap() {
        assert!(stdout.contains("ELEVATION_REQUIRED"));
    }

    let rejected = Command::new(executable)
        .args(["run", "--binary", r"D:\untrusted\codex.exe"])
        .output()
        .expect("run rejected grammar");
    assert_eq!(rejected.status.code(), Some(2));

    let rejected_preflight = Command::new(executable)
        .args(["preflight", "--binary", r"D:\untrusted\codex.exe"])
        .output()
        .expect("run rejected preflight grammar");
    assert_eq!(rejected_preflight.status.code(), Some(2));
}

#[test]
fn diagnostic_cli_is_exact_and_can_only_return_blocked_diagnostic() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let canary = "CANARY_diagnostic_ambient_auth_proxy_must_not_cross_boundary_91d4";
    let output = Command::new(executable)
        .arg("diagnose-preflight")
        .env("OPENAI_API_KEY", canary)
        .env("CHATGPT_SESSION_TOKEN", canary)
        .env("OPENAI_BASE_URL", canary)
        .env("CODEX_HOME", canary)
        .env("HTTP_PROXY", format!("http://user:{canary}@127.0.0.1:9"))
        .env("HTTPS_PROXY", format!("http://user:{canary}@127.0.0.1:9"))
        .env("ALL_PROXY", format!("http://user:{canary}@127.0.0.1:9"))
        .output()
        .expect("run bounded native diagnosis");
    assert_eq!(output.status.code(), Some(3));
    let stdout = String::from_utf8(output.stdout).expect("diagnostic UTF-8");
    assert!(stdout.contains("\"terminal\":\"BLOCKED_DIAGNOSTIC\""));
    assert!(stdout.contains("\"production\":false"));
    assert!(stdout.contains("\"live\":false"));
    assert!(stdout.contains("\"network_usage\":\"UNOBSERVED\""));
    assert!(stdout.contains("\"credential_access\":\"UNOBSERVED\""));
    assert!(!stdout.contains(canary));

    let receipt_paths = json_string_values(&stdout, "receipt_path");
    assert_eq!(receipt_paths.len(), 5);
    let mut variant_receipts = 0;
    for receipt_path in receipt_paths {
        let receipt_path = std::path::PathBuf::from(receipt_path);
        let receipt = std::fs::read_to_string(&receipt_path).expect("read diagnostic receipt");
        assert!(!receipt.contains(canary));
        if receipt_path.file_name().and_then(|name| name.to_str())
            == Some("diagnostic-receipt.json")
        {
            variant_receipts += 1;
            assert!(receipt.contains("\"argv\":[\"--version\"]"));
            assert!(receipt.contains("\"ALL_PROXY\":\"\""));
            assert!(receipt.contains("\"HTTP_PROXY\":\"\""));
            assert!(receipt.contains("\"HTTPS_PROXY\":\"\""));
            assert!(receipt.contains("\"NO_PROXY\":\"*\""));
            assert!(receipt.contains("\\empty-home\""));
            assert!(!receipt.contains("OPENAI_API_KEY"));
            assert!(!receipt.contains("CHATGPT_SESSION_TOKEN"));
            assert!(!receipt.contains("OPENAI_BASE_URL"));
            let run_root = receipt_path.parent().expect("variant run root");
            for artifact in ["stdout.raw", "stderr.raw"] {
                let bytes = std::fs::read(run_root.join("output").join(artifact))
                    .expect("read bounded raw output artifact");
                assert!(!bytes
                    .windows(canary.len())
                    .any(|window| window == canary.as_bytes()));
            }
        }
    }
    assert_eq!(variant_receipts, 4);

    let rejected = Command::new(executable)
        .args(["diagnose-preflight", "B"])
        .output()
        .expect("reject caller-selected diagnostic variant");
    assert_eq!(rejected.status.code(), Some(2));
}

#[test]
fn helper_diagnostic_cli_is_exact_and_always_blocked_with_unobserved_authorities() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let canary = "CANARY_helper_diagnostic_ambient_auth_proxy_must_not_cross_81e7";
    let output = Command::new(executable)
        .arg("diagnose-helper")
        .env("OPENAI_API_KEY", canary)
        .env("CHATGPT_SESSION_TOKEN", canary)
        .env("OPENAI_BASE_URL", canary)
        .env("CODEX_HOME", canary)
        .env("HTTP_PROXY", format!("http://user:{canary}@127.0.0.1:9"))
        .env("HTTPS_PROXY", format!("http://user:{canary}@127.0.0.1:9"))
        .env("ALL_PROXY", format!("http://user:{canary}@127.0.0.1:9"))
        .output()
        .expect("run bounded helper diagnosis");
    assert_eq!(output.status.code(), Some(3));
    let stdout = String::from_utf8(output.stdout).expect("helper diagnostic UTF-8");
    assert!(stdout.contains("\"terminal\":\"BLOCKED_HELPER_DIAGNOSTIC\""));
    assert!(stdout.contains("\"production\":false"));
    assert!(stdout.contains("\"live\":false"));
    assert!(stdout.contains("\"network_usage\":\"UNOBSERVED\""));
    assert!(stdout.contains("\"credential_access\":\"UNOBSERVED\""));
    assert!(stdout.contains("\"external_file_access\":\"UNOBSERVED\""));
    assert!(stdout.contains("FILE_ACCESS_ETW_TRACE_REQUIRED"));
    assert!(stdout.contains("OBSERVED_NON_CODEX_CONHOST_DESCENDANT"));
    assert!(stdout.contains("JOB_ACTIVE_PROCESS_LIMIT_ONE_OBSERVED_WITH_TWO_JOB_PROCESSES"));
    assert!(!stdout.contains(canary));

    let mut artifact_paths = Vec::new();
    for key in [
        "receipt_path",
        "process_trace_path",
        "job_trace_path",
        "home_change_trace_path",
        "home_inventory_path",
    ] {
        artifact_paths.extend(json_string_values(&stdout, key));
    }
    assert_eq!(artifact_paths.len(), 21);
    for artifact_path in artifact_paths {
        let bytes = std::fs::read(&artifact_path).expect("read helper diagnostic artifact");
        assert!(!bytes
            .windows(canary.len())
            .any(|window| window == canary.as_bytes()));
    }

    let rejected = Command::new(executable)
        .args(["diagnose-helper", "B"])
        .output()
        .expect("reject caller-selected helper variant");
    assert_eq!(rejected.status.code(), Some(2));
}

#[test]
fn helper_diagnostic_binds_process_job_and_codex_home_evidence_but_never_unlocks() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let result = run_native_helper_diagnostic();

    assert_eq!(result.exit_code, 3);
    assert_eq!(result.terminal, "BLOCKED_HELPER_DIAGNOSTIC");
    assert!(!result.production);
    assert!(!result.live);
    assert_eq!(result.network_usage, "UNOBSERVED");
    assert_eq!(result.credential_access, "UNOBSERVED");
    assert_eq!(result.external_file_access, "UNOBSERVED");
    assert!(result.blockers.contains(&"FILE_ACCESS_ETW_TRACE_REQUIRED"));
    assert!(result
        .blockers
        .contains(&"OBSERVED_NON_CODEX_CONHOST_DESCENDANT"));
    assert!(result
        .blockers
        .contains(&"JOB_ACTIVE_PROCESS_LIMIT_ONE_OBSERVED_WITH_TWO_JOB_PROCESSES"));
    assert!(!result
        .blockers
        .contains(&"HELPER_DIAGNOSTIC_EVIDENCE_INCOMPLETE"));
    assert_eq!(result.variants.len(), 4);
    assert_eq!(
        result
            .variants
            .iter()
            .map(|variant| (
                variant.id,
                variant.atomic_job_list,
                variant.child_process_restricted,
                variant.assigned_after_create,
                variant.console_creation_mode,
            ))
            .collect::<Vec<_>>(),
        vec![
            ("B", true, false, false, "CREATE_NO_WINDOW"),
            ("D", false, false, true, "CREATE_NO_WINDOW"),
            ("E", true, false, false, "DETACHED_PROCESS"),
            ("F", true, true, false, "DETACHED_PROCESS"),
        ]
    );
    let summary_path = result.receipt_path.as_ref().expect("summary path");
    assert_eq!(
        result.receipt_sha256.as_deref(),
        Some(file_sha256(summary_path).as_str())
    );
    for variant in &result.variants {
        for (path, reported) in [
            (&variant.receipt_path, &variant.receipt_sha256),
            (&variant.process_trace_path, &variant.process_trace_sha256),
            (&variant.job_trace_path, &variant.job_trace_sha256),
            (
                &variant.home_change_trace_path,
                &variant.home_change_trace_sha256,
            ),
            (&variant.home_inventory_path, &variant.home_inventory_sha256),
        ] {
            let path = path.as_ref().expect("artifact path");
            assert_eq!(
                reported.as_deref(),
                Some(file_sha256(path).as_str()),
                "reported artifact hash must match bytes: {}",
                path.display()
            );
        }
    }
    for variant in result
        .variants
        .iter()
        .filter(|variant| matches!(variant.id, "B" | "D"))
    {
        assert_eq!(variant.terminal, "BLOCKED_HELPER_DIAGNOSTIC");
        assert!(!variant.production);
        assert!(!variant.live);
        assert_eq!(variant.network_usage, "UNOBSERVED");
        assert_eq!(variant.credential_access, "UNOBSERVED");
        assert_eq!(variant.external_file_access, "UNOBSERVED");
        assert_eq!(variant.blocker, "OBSERVED_NON_CODEX_CONHOST_DESCENDANT");
        assert_eq!(
            variant.blockers,
            vec![
                "HELPER_DIAGNOSTIC_ONLY_NEVER_QUALIFIES",
                "OBSERVED_NON_CODEX_CONHOST_DESCENDANT",
                "JOB_ACTIVE_PROCESS_LIMIT_ONE_OBSERVED_WITH_TWO_JOB_PROCESSES",
            ]
        );
        assert!(variant.descendants_zero);
        assert_eq!(variant.processes.len(), 2);
        let root = &variant.processes[0];
        let conhost = &variant.processes[1];
        assert!(root.in_job);
        assert_eq!(root.image_role, "PINNED_CODEX_ROOT");
        assert_eq!(root.image_sha256, EXPECTED_CODEX_EXE_SHA256);
        assert!(root.image_matches_pinned_binary);
        assert!(!root.image_matches_pinned_conhost);
        assert!(root.command_line_matches_expected);
        assert_eq!(conhost.image_role, "FIXED_CONTENT_CONHOST_DESCENDANT");
        assert_eq!(
            conhost.image_sha256,
            "sha256:7ced551ec8afab3391a3ede5442046558e4799dcfd631ac9252fe93929a3465e"
        );
        assert!(!conhost.image_matches_pinned_binary);
        assert!(conhost.image_matches_pinned_conhost);
        assert_eq!(conhost.image_signature, "UNOBSERVED");
        assert_eq!(conhost.windows_build_binding, "UNQUALIFIED");
        assert!(conhost.command_line_matches_expected);
        assert_eq!(variant.processes[0].parent_process_id, std::process::id());
        assert_eq!(
            variant.processes[1].parent_process_id,
            variant.processes[0].process_id
        );
        assert!(variant.job_events_complete);
        assert!(variant.debug_events_complete);
        assert_eq!(variant.debug_events.len(), 4);
        assert_eq!(
            variant
                .debug_events
                .iter()
                .map(|event| (event.kind, event.process_id))
                .collect::<Vec<_>>(),
            vec![
                ("CREATE_PROCESS", root.process_id),
                ("CREATE_PROCESS", conhost.process_id),
                ("EXIT_PROCESS", root.process_id),
                ("EXIT_PROCESS", conhost.process_id),
            ]
        );
        assert!(variant
            .job_events
            .iter()
            .any(|event| event.kind == "ACTIVE_PROCESS_ZERO"));
        assert!(variant.codex_home_change_trace_complete);
        assert!(!variant.codex_home_inventory.is_empty());
        assert!(variant
            .codex_home_inventory
            .iter()
            .any(|entry| entry.relative_path.ends_with("apply_patch.bat")));
        assert!(variant
            .receipt_path
            .as_ref()
            .is_some_and(|path| path.is_file()));
        assert!(variant
            .receipt_sha256
            .as_deref()
            .is_some_and(|hash| hash.starts_with("sha256:")));
    }
}

#[test]
fn detached_console_probe_observes_only_the_pinned_codex_root_process() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let result = run_native_helper_diagnostic();

    assert_eq!(result.exit_code, 3);
    assert_eq!(result.terminal, "BLOCKED_HELPER_DIAGNOSTIC");
    assert!(!result.production);
    assert!(!result.live);
    assert_eq!(result.variants.len(), 4);

    let detached = result
        .variants
        .iter()
        .find(|variant| variant.id == "E")
        .expect("fixed DETACHED_PROCESS diagnostic variant");
    assert!(detached.child_created);
    assert!(detached.child_resumed);
    assert!(detached.descendants_zero);
    assert_eq!(detached.exit_code, Some(0));
    assert_eq!(detached.final_total_processes, Some(1));
    assert_eq!(detached.final_active_processes, Some(0));
    assert_eq!(detached.max_observed_total_processes, Some(1));
    assert_eq!(detached.max_observed_active_processes, Some(1));
    assert_eq!(detached.processes.len(), 1);
    assert_eq!(detached.processes[0].image_role, "PINNED_CODEX_ROOT");
    assert!(detached.processes[0].image_matches_pinned_binary);
    assert!(!detached
        .processes
        .iter()
        .any(|process| process.image_matches_pinned_conhost));
    assert!(!detached
        .blockers
        .contains(&"OBSERVED_NON_CODEX_CONHOST_DESCENDANT"));
    assert!(!detached
        .blockers
        .contains(&"JOB_ACTIVE_PROCESS_LIMIT_ONE_OBSERVED_WITH_TWO_JOB_PROCESSES"));
    assert!(detached
        .blockers
        .contains(&"HELPER_DIAGNOSTIC_ONLY_NEVER_QUALIFIES"));
    assert_eq!(detached.blocker, "HELPER_DIAGNOSTIC_ONLY_NEVER_QUALIFIES");
    assert_eq!(detached.console_creation_mode, "DETACHED_PROCESS");
    assert!(detached.child_process_policy_readback);
    assert_eq!(detached.child_process_policy_flags, 0);
    assert!(detached.debug_events_complete);
    assert!(detached.job_events_complete);
    assert_eq!(detached.debug_events.len(), 2);
    assert_eq!(detached.job_events.len(), 3);
    assert!(detached.codex_home_change_trace_complete);
    assert!(detached.codex_home_inventory_complete);
    assert!(detached
        .receipt_path
        .as_ref()
        .is_some_and(|path| path.is_file()));
    assert!(result
        .to_json()
        .contains("\"id\":\"E\",\"console_creation_mode\":\"DETACHED_PROCESS\""));
}

#[test]
fn detached_console_and_child_restriction_can_start_without_a_descendant() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let result = run_native_helper_diagnostic();

    assert_eq!(result.exit_code, 3);
    assert_eq!(result.variants.len(), 4);
    let restricted = result
        .variants
        .iter()
        .find(|variant| variant.id == "F")
        .expect("fixed DETACHED_PROCESS + CHILD_PROCESS_RESTRICTED variant");
    assert!(restricted.atomic_job_list);
    assert!(restricted.child_process_restricted);
    assert!(restricted.child_process_policy_readback);
    assert_eq!(restricted.child_process_policy_flags, 1);
    assert!(!restricted.assigned_after_create);
    assert!(restricted.child_created);
    assert!(restricted.child_resumed);
    assert!(restricted.descendants_zero);
    assert_eq!(restricted.exit_code, Some(0));
    assert_eq!(restricted.final_total_processes, Some(1));
    assert_eq!(restricted.max_observed_total_processes, Some(1));
    assert_eq!(restricted.processes.len(), 1);
    assert!(restricted.processes[0].image_matches_pinned_binary);
    assert!(!restricted
        .processes
        .iter()
        .any(|process| process.image_matches_pinned_conhost));
    assert_eq!(restricted.console_creation_mode, "DETACHED_PROCESS");
    assert!(restricted.debug_events_complete);
    assert!(restricted.job_events_complete);
    assert!(restricted.codex_home_change_trace_complete);
    assert!(restricted.codex_home_inventory_complete);
    assert!(!restricted
        .blockers
        .contains(&"OBSERVED_NON_CODEX_CONHOST_DESCENDANT"));
}

#[test]
fn bounded_diagnostic_runs_all_four_variants_and_persists_fail_closed_receipts() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let result = run_native_preflight_diagnostic();

    assert_eq!(result.exit_code, 3);
    assert_eq!(result.terminal, "BLOCKED_DIAGNOSTIC");
    assert_eq!(result.blocker, "DIAGNOSTIC_ONLY_NEVER_QUALIFIES");
    assert!(result.decision_required);
    assert_eq!(
        result.finding,
        "CHILD_PROCESS_RESTRICTION_TRUE_OBSERVED_C0000142_WITH_ZERO_ADDITIONAL_JOB_PROCESSES_FALSE_OBSERVED_EXIT0_WITH_ONE_ADDITIONAL_JOB_PROCESS_IN_BOTH_JOB_ASSIGNMENT_MODES"
    );
    assert_eq!(result.variants.len(), 4);
    assert_eq!(
        result
            .variants
            .iter()
            .map(|variant| {
                (
                    variant.id,
                    variant.atomic_job_list,
                    variant.child_process_restricted,
                    variant.assigned_after_create,
                )
            })
            .collect::<Vec<_>>(),
        vec![
            ("A", true, true, false),
            ("B", true, false, false),
            ("C", false, true, true),
            ("D", false, false, true),
        ]
    );
    for variant in &result.variants {
        assert_eq!(variant.terminal, "BLOCKED_DIAGNOSTIC");
        assert!(!variant.production);
        assert!(!variant.live);
        assert!(!variant.auth_isolation);
        assert!(!variant.network_enforcement);
        assert_eq!(variant.network_usage, "UNOBSERVED");
        assert_eq!(variant.credential_access, "UNOBSERVED");
        assert!(variant
            .receipt_path
            .as_ref()
            .is_some_and(|path| path.is_file()));
        assert!(variant
            .receipt_sha256
            .as_deref()
            .is_some_and(|hash| hash.starts_with("sha256:")));
        if variant.child_created {
            assert!(variant.descendants_zero);
        }
        assert!(!variant.stdout_overflow);
        assert!(!variant.stderr_overflow);
    }
    assert_eq!(
        result
            .variants
            .iter()
            .map(|variant| (
                variant.id,
                variant.blocker,
                variant.exit_code,
                variant.final_total_processes,
                variant.max_additional_job_processes_seen,
                variant.stdout_total_bytes,
                variant.stdout_sha256.as_deref(),
            ))
            .collect::<Vec<_>>(),
        vec![
            (
                "A",
                "CODEX_0147_DLL_INIT_FAILED_UNDER_VARIANT",
                Some(3_221_225_794),
                Some(1),
                Some(0),
                0,
                Some("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
            ),
            (
                "B",
                "DIAGNOSTIC_VERSION_OUTPUT_OBSERVED",
                Some(0),
                Some(2),
                Some(1),
                18,
                Some("sha256:47e8650c39eae3ea896e5873f03a97a65d183d81cd0d86616e8b83d7d87877ca"),
            ),
            (
                "C",
                "CODEX_0147_DLL_INIT_FAILED_UNDER_VARIANT",
                Some(3_221_225_794),
                Some(1),
                Some(0),
                0,
                Some("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
            ),
            (
                "D",
                "DIAGNOSTIC_VERSION_OUTPUT_OBSERVED",
                Some(0),
                Some(2),
                Some(1),
                18,
                Some("sha256:47e8650c39eae3ea896e5873f03a97a65d183d81cd0d86616e8b83d7d87877ca"),
            ),
        ]
    );
    // TotalProcesses/max_additional are cumulative Job facts. The sampled
    // ActiveProcesses value may legitimately miss the short-lived conhost
    // overlap, so it is evidence metadata rather than the child-count gate.
    assert_eq!(result.variants[0].max_observed_active_processes, Some(1));
    assert!(matches!(
        result.variants[1].max_observed_active_processes,
        Some(1 | 2)
    ));
    assert_eq!(result.variants[2].max_observed_active_processes, Some(1));
    assert!(matches!(
        result.variants[3].max_observed_active_processes,
        Some(1 | 2)
    ));
    assert!(result
        .receipt_path
        .as_ref()
        .is_some_and(|path| path.is_file()));
    assert!(result
        .receipt_sha256
        .as_deref()
        .is_some_and(|hash| hash.starts_with("sha256:")));
}

#[test]
fn exact_preflight_uses_detached_process_and_reaches_the_auth_isolation_gate() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let canary = "CANARY_preflight_must_not_reach_child_or_receipt_7f2c1a";
    let output = Command::new(executable)
        .arg("preflight")
        .env("OPENAI_API_KEY", canary)
        .env("HTTPS_PROXY", format!("http://user:{canary}@127.0.0.1:9"))
        .output()
        .expect("run exact native preflight");

    assert_eq!(output.status.code(), Some(3));
    let stdout = String::from_utf8(output.stdout).expect("preflight UTF-8");
    let stderr = String::from_utf8(output.stderr).expect("preflight stderr UTF-8");
    assert!(stderr.is_empty());
    assert!(stdout.contains("\"terminal\":\"BLOCKED_PREFLIGHT\""));
    assert!(stdout.contains("\"blocker\":\"PREFLIGHT_FULL_TOKEN_AUTH_ISOLATION_NOT_QUALIFIED\""));
    assert!(stdout.contains("\"mode\":\"PREFLIGHT_ONLY\""));
    assert!(stdout.contains("\"live\":false"));
    assert!(stdout.contains("\"auth_materialized_by_launcher\":false"));
    assert!(stdout.contains("\"network_requested\":false"));
    assert!(stdout.contains("\"auth_isolation\":false"));
    assert!(stdout.contains("\"network_enforcement\":false"));
    assert!(stdout.contains("\"network_usage\":\"UNOBSERVED\""));
    assert!(stdout.contains("\"credential_access\":\"UNOBSERVED\""));
    assert!(stdout.contains("\"child_created\":true"));
    assert!(stdout.contains("\"child_resumed\":true"));
    assert!(stdout.contains("\"descendants_zero\":true"));
    assert!(stdout.contains("\"receipt_sha256\":\"sha256:"));
    assert!(!stdout.contains(canary));

    let receipt_marker = "\"receipt_path\":\"";
    let receipt_start =
        stdout.find(receipt_marker).expect("receipt path field") + receipt_marker.len();
    let receipt_tail = &stdout[receipt_start..];
    let receipt_end = receipt_tail.find('"').expect("receipt path terminator");
    let receipt_path = receipt_tail[..receipt_end].replace("\\\\", "\\");
    let receipt_path = std::path::PathBuf::from(receipt_path);
    let receipt_hashes = json_string_values(&stdout, "receipt_sha256");
    assert_eq!(receipt_hashes.len(), 1);
    assert_eq!(receipt_hashes[0], file_sha256(&receipt_path));
    let receipt = std::fs::read_to_string(&receipt_path).expect("read immutable receipt");
    assert!(receipt.contains("\"job_total_processes\":1"));
    assert!(receipt.contains("\"job_active_processes\":0"));
    assert!(receipt.contains("\"pre_resume_job_total_processes\":1"));
    assert!(receipt.contains("\"pre_resume_job_active_processes\":1"));
    assert!(receipt.contains("\"console_creation_mode\":\"DETACHED_PROCESS\""));
    assert!(receipt.contains("\"child_process_policy_readback\":true"));
    assert!(receipt.contains("\"child_process_policy_flags\":1"));
    assert!(receipt.contains("\"exit_code\":0"));
    assert!(!receipt.contains(canary));
}

#[test]
fn child_policy_canary_proves_the_os_rejects_a_real_child_creation_attempt() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let canary = env!("CARGO_BIN_EXE_pkm-child-policy-canary");
    assert!(std::path::Path::new(canary).is_file());
    let unrestricted = Command::new(canary)
        .output()
        .expect("run child-policy canary without the policy");
    assert_eq!(unrestricted.status.code(), Some(3));
    assert!(unrestricted.stderr.is_empty());
    assert!(String::from_utf8(unrestricted.stdout)
        .expect("unrestricted canary UTF-8")
        .contains("\"terminal\":\"CHILD_CREATION_ALLOWED\""));
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let output = Command::new(executable)
        .arg("diagnose-child-policy")
        .output()
        .expect("run fixed child-policy canary diagnosis");

    assert_eq!(output.status.code(), Some(3));
    assert!(output.stderr.is_empty());
    let stdout = String::from_utf8(output.stdout).expect("canary diagnostic UTF-8");
    assert!(stdout.contains("\"terminal\":\"BLOCKED_CHILD_POLICY_DIAGNOSTIC\""));
    assert!(stdout.contains("\"production\":false"));
    assert!(stdout.contains("\"live\":false"));
    assert!(stdout.contains("\"console_creation_mode\":\"DETACHED_PROCESS\""));
    assert!(stdout.contains("\"child_process_policy_readback\":true"));
    assert!(stdout.contains("\"child_process_policy_flags\":1"));
    assert!(stdout.contains("\"canary_child_creation_attempted\":true"));
    assert!(stdout.contains("\"canary_child_creation_blocked\":true"));
    assert!(stdout.contains("\"job_total_processes\":1"));
    assert!(stdout.contains("\"job_active_processes\":0"));
    assert!(stdout.contains("\"descendants_zero\":true"));
    assert!(stdout.contains("\"receipt_sha256\":\"sha256:"));
    let receipt_paths = json_string_values(&stdout, "receipt_path");
    let receipt_hashes = json_string_values(&stdout, "receipt_sha256");
    assert_eq!(receipt_paths.len(), 1);
    assert_eq!(receipt_hashes.len(), 1);
    assert_eq!(
        receipt_hashes[0],
        file_sha256(std::path::Path::new(&receipt_paths[0]))
    );
    let rejected = Command::new(executable)
        .args(["diagnose-child-policy", "caller-selected-path"])
        .output()
        .expect("reject caller-controlled canary arguments");
    assert_eq!(rejected.status.code(), Some(2));
}

#[test]
fn appcontainer_profile_diagnostic_recreates_and_removes_zero_capability_identity() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let output = Command::new(executable)
        .arg("diagnose-appcontainer-profile")
        .output()
        .expect("run fixed AppContainer profile diagnosis");

    assert_eq!(output.status.code(), Some(3));
    assert!(output.stderr.is_empty());
    let stdout = String::from_utf8(output.stdout).expect("profile diagnostic UTF-8");
    assert!(stdout.contains("\"terminal\":\"BLOCKED_APPCONTAINER_PROFILE_DIAGNOSTIC\""));
    assert!(stdout.contains("\"production\":false"));
    assert!(stdout.contains("\"live\":false"));
    assert!(stdout.contains("\"zero_capabilities\":true"));
    assert!(stdout.contains("\"first_create\":true"));
    assert!(stdout.contains("\"derived_sid_match\":true"));
    assert!(stdout.contains("\"first_delete\":true"));
    assert!(stdout.contains("\"recreate\":true"));
    assert!(stdout.contains("\"sid_stable\":true"));
    assert!(stdout.contains("\"final_delete\":true"));
    assert!(stdout.contains("\"profile_name_sha256\":\"sha256:"));
    assert!(stdout.contains("\"package_sid_sha256\":\"sha256:"));
    assert!(stdout.contains("\"receipt_sha256\":\"sha256:"));
    assert!(!stdout.contains("OpenAI.PKMWorkflow.Codex.T13b."));

    let receipt_paths = json_string_values(&stdout, "receipt_path");
    let receipt_hashes = json_string_values(&stdout, "receipt_sha256");
    assert_eq!(receipt_paths.len(), 1);
    assert_eq!(receipt_hashes.len(), 1);
    assert_eq!(
        receipt_hashes[0],
        file_sha256(std::path::Path::new(&receipt_paths[0]))
    );
    let receipt =
        std::fs::read_to_string(&receipt_paths[0]).expect("read profile lifecycle receipt");
    let run_ids = json_string_values(&receipt, "run_id");
    assert_eq!(run_ids.len(), 1);
    let profile_name = format!("OpenAI.PKMWorkflow.Codex.T13b.{}", run_ids[0]);
    let profile_name = wide_null(std::ffi::OsStr::new(&profile_name));
    let display_name = wide_null(std::ffi::OsStr::new("PKM independent absence observer"));
    let description = wide_null(std::ffi::OsStr::new(
        "Recreate proves the diagnostic left no registered profile",
    ));
    let mut sid = std::ptr::null_mut();
    let create_status = unsafe {
        CreateAppContainerProfile(
            profile_name.as_ptr(),
            display_name.as_ptr(),
            description.as_ptr(),
            std::ptr::null(),
            0,
            &mut sid,
        )
    };
    assert_eq!(create_status, 0, "diagnostic left a registered profile");
    assert!(!sid.is_null() && unsafe { IsValidSid(sid) } != 0);
    let delete_status = unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) };
    let _ = unsafe { FreeSid(sid) };
    assert_eq!(delete_status, 0, "independent observer cleanup failed");

    let rejected = Command::new(executable)
        .args(["diagnose-appcontainer-profile", "caller-name"])
        .output()
        .expect("reject caller-controlled profile name");
    assert_eq!(rejected.status.code(), Some(2));
}

#[test]
fn wfp_policy_diagnostic_requires_elevation_before_any_os_mutation() {
    if process_is_elevated().expect("query elevation") {
        return;
    }
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let output = Command::new(executable)
        .arg("diagnose-wfp-policy")
        .output()
        .expect("run fixed WFP policy diagnosis");

    assert_eq!(output.status.code(), Some(3));
    assert!(output.stderr.is_empty());
    let stdout = String::from_utf8(output.stdout).expect("WFP diagnostic UTF-8");
    assert!(stdout.contains("\"terminal\":\"BLOCKED_WFP_POLICY_DIAGNOSTIC\""));
    assert!(stdout.contains("\"blocker\":\"ELEVATION_REQUIRED\""));
    assert!(stdout.contains("\"production\":false"));
    assert!(stdout.contains("\"live\":false"));
    assert!(stdout.contains("\"profile_created\":false"));
    assert!(stdout.contains("\"wfp_installed\":false"));
    assert!(stdout.contains("\"filter_readback\":false"));
    assert!(stdout.contains("\"cleanup_verified\":false"));
    assert!(stdout.contains("\"receipt_path\":null"));

    let rejected = Command::new(executable)
        .args(["diagnose-wfp-policy", "caller-port"])
        .output()
        .expect("reject caller-controlled WFP arguments");
    assert_eq!(rejected.status.code(), Some(2));
}

#[test]
fn lpac_token_diagnostic_records_direct_readback_blocker_before_any_resume() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let output = Command::new(executable)
        .arg("diagnose-lpac-token")
        .output()
        .expect("run fixed LPAC token diagnosis");

    assert_eq!(output.status.code(), Some(3));
    assert!(output.stderr.is_empty());
    let stdout = String::from_utf8(output.stdout).expect("LPAC diagnostic UTF-8");
    assert!(stdout.contains("\"terminal\":\"BLOCKED_LPAC_TOKEN_DIAGNOSTIC\""));
    assert!(stdout.contains("\"blocker\":\"LPAC_DIRECT_TOKEN_READBACK_UNSUPPORTED\""));
    assert!(stdout.contains("\"production\":false"));
    assert!(stdout.contains("\"live\":false"));
    assert!(stdout.contains("\"auth_materialized_by_launcher\":false"));
    assert!(stdout.contains("\"network_requested\":false"));
    assert!(stdout.contains("\"profile_created\":true"));
    assert!(stdout.contains("\"child_created_suspended\":true"));
    assert!(stdout.contains("\"child_resumed\":false"));
    assert!(stdout.contains("\"lpac_attribute_accepted\":true"));
    assert!(stdout.contains("\"job_membership_verified\":true"));
    assert!(stdout.contains("\"child_process_restricted\":true"));
    assert!(stdout.contains("\"token_is_appcontainer\":true"));
    assert!(stdout.contains("\"token_is_lpac\":null"));
    assert!(stdout.contains("\"lpac_direct_query_supported\":false"));
    assert!(stdout.contains("\"capability_count\":0"));
    assert!(stdout.contains("\"package_sid_match\":true"));
    assert!(stdout.contains("\"descendants_zero\":true"));
    assert!(stdout.contains("\"profile_deleted\":true"));
    assert!(stdout.contains("\"receipt_sha256\":\"sha256:"));

    let receipt_paths = json_string_values(&stdout, "receipt_path");
    let receipt_hashes = json_string_values(&stdout, "receipt_sha256");
    assert_eq!(receipt_paths.len(), 1);
    assert_eq!(receipt_hashes.len(), 1);
    assert_eq!(
        receipt_hashes[0],
        file_sha256(std::path::Path::new(&receipt_paths[0]))
    );
    let receipt = std::fs::read_to_string(&receipt_paths[0]).expect("read LPAC receipt");
    assert!(receipt.contains("\"blocker\":\"LPAC_DIRECT_TOKEN_READBACK_UNSUPPORTED\""));
    assert!(receipt.contains("\"token_is_lpac\":null"));
    assert!(receipt.contains("\"lpac_direct_query_supported\":false"));
    assert!(receipt.contains("\"profile_deleted\":true"));

    let rejected = Command::new(executable)
        .args(["diagnose-lpac-token", "caller-binary"])
        .output()
        .expect("reject caller-controlled LPAC arguments");
    assert_eq!(rejected.status.code(), Some(2));
}

#[test]
fn appcontainer_creator_kill_reconciliation_cleans_a_real_profile() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let output = Command::new(executable)
        .arg("diagnose-appcontainer-creator-kill-reconciliation")
        .output()
        .expect("run fixed AppContainer creator-kill reconciliation diagnosis");

    assert_eq!(output.status.code(), Some(3));
    assert!(output.stderr.is_empty());
    let stdout = String::from_utf8(output.stdout).expect("creator-kill diagnostic UTF-8");
    for expected in [
        "\"terminal\":\"BLOCKED_APPCONTAINER_CREATOR_KILL_RECONCILIATION_DIAGNOSTIC\"",
        "\"blocker\":\"APPCONTAINER_CREATOR_KILL_RECONCILIATION_OBSERVED_NOT_RELEASE_BOUND\"",
        "\"production\":false",
        "\"live\":false",
        "\"auth_materialized_by_launcher\":false",
        "\"network_requested\":false",
        "\"canary_ready\":true",
        "\"canary_timed_out\":true",
        "\"canary_resumed\":true",
        "\"descendants_zero\":true",
        "\"stale_profile_deleted\":true",
        "\"recreate\":true",
        "\"sid_stable\":true",
        "\"final_delete\":true",
        "\"owner_restart_recovery_observed\":false",
        "\"receipt_sha256\":\"sha256:",
    ] {
        assert!(stdout.contains(expected), "missing {expected}: {stdout}");
    }
    let receipt_paths = json_string_values(&stdout, "receipt_path");
    let receipt_hashes = json_string_values(&stdout, "receipt_sha256");
    assert_eq!(receipt_paths.len(), 1);
    assert_eq!(receipt_hashes.len(), 1);
    assert_eq!(
        receipt_hashes[0],
        file_sha256(std::path::Path::new(&receipt_paths[0]))
    );

    let receipt = std::fs::read_to_string(&receipt_paths[0]).expect("read creator-kill receipt");
    let run_ids = json_string_values(&receipt, "run_id");
    assert_eq!(run_ids.len(), 1);
    let profile_name = format!("OpenAI.PKMWorkflow.Codex.T13b.{}", run_ids[0]);
    let profile_name = wide_null(std::ffi::OsStr::new(&profile_name));
    let display_name = wide_null(std::ffi::OsStr::new("PKM creator-kill absence observer"));
    let description = wide_null(std::ffi::OsStr::new(
        "Independent recreate proves the reconciliation left no profile",
    ));
    let mut sid = std::ptr::null_mut();
    let create_status = unsafe {
        CreateAppContainerProfile(
            profile_name.as_ptr(),
            display_name.as_ptr(),
            description.as_ptr(),
            std::ptr::null(),
            0,
            &mut sid,
        )
    };
    assert_eq!(create_status, 0, "reconciliation left a registered profile");
    assert!(!sid.is_null() && unsafe { IsValidSid(sid) } != 0);
    let delete_status = unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) };
    let _ = unsafe { FreeSid(sid) };
    assert_eq!(
        delete_status, 0,
        "independent reconciliation cleanup failed"
    );

    let rejected = Command::new(executable)
        .args([
            "diagnose-appcontainer-creator-kill-reconciliation",
            "caller-profile",
        ])
        .output()
        .expect("reject caller-controlled reconciliation arguments");
    assert_eq!(rejected.status.code(), Some(2));
}

#[test]
fn appcontainer_owner_restart_uses_flushed_intent_and_a_fresh_recovery_process() {
    let _guard = NATIVE_CHILD_TEST_LOCK
        .lock()
        .expect("native child test lock");
    let executable = env!("CARGO_BIN_EXE_pkm-codex-qualifier");
    let output = Command::new(executable)
        .arg("diagnose-appcontainer-owner-restart-recovery")
        .output()
        .expect("run fixed owner-restart recovery diagnosis");

    assert_eq!(output.status.code(), Some(3));
    assert!(output.stderr.is_empty());
    let stdout = String::from_utf8(output.stdout).expect("owner-restart diagnostic UTF-8");
    for expected in [
        "\"terminal\":\"BLOCKED_APPCONTAINER_OWNER_RESTART_RECOVERY_DIAGNOSTIC\"",
        "\"blocker\":\"APPCONTAINER_OWNER_RESTART_RECOVERY_OBSERVED_NOT_RELEASE_BOUND\"",
        "\"production\":false",
        "\"live\":false",
        "\"phase_a_intent_file_flushed_and_observed\":true",
        "\"phase_a_ready\":true",
        "\"phase_a_timed_out\":true",
        "\"phase_a_descendants_zero\":true",
        "\"phase_b_fresh_process\":true",
        "\"phase_b_intent_verified\":true",
        "\"phase_b_descendants_zero\":true",
        "\"phase_b_stale_profile_deleted\":true",
        "\"phase_b_recreate\":true",
        "\"phase_b_sid_stable\":true",
        "\"phase_b_final_delete\":true",
        "\"owner_process_restart_recovery_observed\":true",
        "\"qualifier_restart_recovery_observed\":false",
        "\"startup_intent_discovery_observed\":false",
        "\"directory_metadata_durability_observed\":false",
        "\"profile_retained_for_recovery\":false",
        "\"receipt_sha256\":\"sha256:",
    ] {
        assert!(stdout.contains(expected), "missing {expected}: {stdout}");
    }
    let receipt_paths = json_string_values(&stdout, "receipt_path");
    let receipt_hashes = json_string_values(&stdout, "receipt_sha256");
    assert_eq!(receipt_paths.len(), 1);
    assert_eq!(receipt_hashes.len(), 1);
    assert_eq!(
        receipt_hashes[0],
        file_sha256(std::path::Path::new(&receipt_paths[0]))
    );

    let receipt = std::fs::read_to_string(&receipt_paths[0]).expect("read owner-restart receipt");
    let run_ids = json_string_values(&receipt, "run_id");
    assert_eq!(run_ids.len(), 1);
    let profile_name = format!("OpenAI.PKMWorkflow.Codex.T13b.{}", run_ids[0]);
    let profile_name = wide_null(std::ffi::OsStr::new(&profile_name));
    let display_name = wide_null(std::ffi::OsStr::new("PKM owner-restart absence observer"));
    let description = wide_null(std::ffi::OsStr::new(
        "Independent recreate proves phase B left no profile",
    ));
    let mut sid = std::ptr::null_mut();
    let create_status = unsafe {
        CreateAppContainerProfile(
            profile_name.as_ptr(),
            display_name.as_ptr(),
            description.as_ptr(),
            std::ptr::null(),
            0,
            &mut sid,
        )
    };
    assert_eq!(create_status, 0, "phase B left a registered profile");
    assert!(!sid.is_null() && unsafe { IsValidSid(sid) } != 0);
    let delete_status = unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) };
    let _ = unsafe { FreeSid(sid) };
    assert_eq!(delete_status, 0, "owner-restart observer cleanup failed");

    let rejected = Command::new(executable)
        .args([
            "diagnose-appcontainer-owner-restart-recovery",
            "caller-intent",
        ])
        .output()
        .expect("reject caller-controlled owner-restart arguments");
    assert_eq!(rejected.status.code(), Some(2));
}
