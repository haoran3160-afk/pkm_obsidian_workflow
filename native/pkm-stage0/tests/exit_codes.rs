use std::process::Command;

#[test]
fn real_process_preserves_stage0_safety_block_exit_code() {
    let executable = env!("CARGO_BIN_EXE_pkm-stage0");
    let output = Command::new(executable)
        .args(["--workflow", "ai", "--cause", "schedule", "--due-only"])
        .output()
        .expect("spawn stage0");

    assert_eq!(output.status.code(), Some(3));
    assert!(String::from_utf8_lossy(&output.stderr).contains("safety blocked"));
}
