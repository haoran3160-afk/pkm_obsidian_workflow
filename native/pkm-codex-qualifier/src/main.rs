use std::env;
use std::process::ExitCode;

use pkm_codex_qualifier::{
    native_static_blockers, process_is_elevated, run_native_appcontainer_profile_diagnostic,
    run_native_child_policy_diagnostic, run_native_helper_diagnostic,
    run_native_lpac_token_diagnostic, run_native_preflight, run_native_preflight_diagnostic,
    run_native_wfp_policy_diagnostic,
};

fn main() -> ExitCode {
    let arguments: Vec<_> = env::args_os().skip(1).collect();
    if arguments.len() != 1 {
        eprintln!(
            "pkm-codex-qualifier: expected exactly `doctor`, `preflight`, `diagnose-preflight`, `diagnose-helper`, `diagnose-child-policy`, `diagnose-appcontainer-profile`, `diagnose-lpac-token`, or `diagnose-wfp-policy`"
        );
        return ExitCode::from(2);
    }
    if arguments[0] == "preflight" {
        let result = run_native_preflight();
        println!("{}", result.to_json());
        return ExitCode::from(result.exit_code);
    }
    if arguments[0] == "diagnose-preflight" {
        let result = run_native_preflight_diagnostic();
        println!("{}", result.to_json());
        return ExitCode::from(result.exit_code);
    }
    if arguments[0] == "diagnose-helper" {
        let result = run_native_helper_diagnostic();
        println!("{}", result.to_json());
        return ExitCode::from(result.exit_code);
    }
    if arguments[0] == "diagnose-child-policy" {
        let result = run_native_child_policy_diagnostic();
        println!("{}", result.to_json());
        return ExitCode::from(result.exit_code);
    }
    if arguments[0] == "diagnose-appcontainer-profile" {
        let result = run_native_appcontainer_profile_diagnostic();
        println!("{}", result.to_json());
        return ExitCode::from(result.exit_code);
    }
    if arguments[0] == "diagnose-wfp-policy" {
        let result = run_native_wfp_policy_diagnostic();
        println!("{}", result.to_json());
        return ExitCode::from(result.exit_code);
    }
    if arguments[0] == "diagnose-lpac-token" {
        let result = run_native_lpac_token_diagnostic();
        println!("{}", result.to_json());
        return ExitCode::from(result.exit_code);
    }
    if arguments[0] != "doctor" {
        eprintln!(
            "pkm-codex-qualifier: expected exactly `doctor`, `preflight`, `diagnose-preflight`, `diagnose-helper`, `diagnose-child-policy`, `diagnose-appcontainer-profile`, `diagnose-lpac-token`, or `diagnose-wfp-policy`"
        );
        return ExitCode::from(2);
    }
    let mut blockers = Vec::new();
    match process_is_elevated() {
        Ok(false) => blockers.push("ELEVATION_REQUIRED"),
        Ok(true) => {}
        Err(_) => blockers.push("TOKEN_ELEVATION_QUERY_FAILED"),
    }
    blockers.extend(native_static_blockers().into_iter().map(|value| value.code));
    let blockers = blockers
        .into_iter()
        .map(|value| format!("\"{value}\""))
        .collect::<Vec<_>>()
        .join(",");
    println!(
        "{{\"terminal\":\"BLOCKED_PREQUALIFICATION\",\"blockers\":[{blockers}],\"child_created\":false,\"child_resumed\":false,\"secret_materialized_in_child\":false,\"network_or_auth_used\":false}}"
    );
    ExitCode::from(3)
}
