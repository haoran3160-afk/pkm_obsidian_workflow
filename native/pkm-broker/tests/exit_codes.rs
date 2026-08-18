use std::process::Command;

#[test]
fn real_process_never_reports_unimplemented_shadow_as_success() {
    let executable = env!("CARGO_BIN_EXE_pkm-broker");
    let shadow = Command::new(executable)
        .args(["shadow", "--workflow", "ai"])
        .output()
        .expect("spawn shadow broker");
    let blocked = Command::new(executable)
        .args(["run", "--workflow", "ai", "--cause", "manual"])
        .output()
        .expect("spawn blocked broker");

    assert_eq!(shadow.status.code(), Some(3));
    assert_eq!(blocked.status.code(), Some(3));
    assert!(String::from_utf8_lossy(&shadow.stdout).contains("SHADOW_ENGINE_NOT_IMPLEMENTED"));
    assert!(String::from_utf8_lossy(&blocked.stdout).contains("NATIVE_QUALIFICATION_INCOMPLETE"));
}
