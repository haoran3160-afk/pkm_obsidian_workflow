use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::process::ExitCode;

use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_CHILD_PROCESS_BLOCKED, WAIT_OBJECT_0,
};
use windows_sys::Win32::System::Threading::{
    CreateProcessW, TerminateProcess, WaitForSingleObject, CREATE_SUSPENDED,
    CREATE_UNICODE_ENVIRONMENT, DETACHED_PROCESS, PROCESS_INFORMATION, STARTUPINFOW,
};

const FIXED_CHILD_PATH: &str = r"C:\Windows\System32\cmd.exe";
const FIXED_CHILD_COMMAND: &str = r#""C:\Windows\System32\cmd.exe" /d /c exit 0"#;
const FIXED_CHILD_CWD: &str = r"C:\Windows\System32";
const UNEXPECTED_CHILD_EXIT: u32 = 0xc000_013a;

fn wide_null(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(std::iter::once(0)).collect()
}

fn main() -> ExitCode {
    if std::env::args_os().nth(1).is_some() {
        eprintln!("pkm-child-policy-canary: expected no arguments");
        return ExitCode::from(2);
    }

    let application = wide_null(OsStr::new(FIXED_CHILD_PATH));
    let mut command_line = wide_null(OsStr::new(FIXED_CHILD_COMMAND));
    let current_directory = wide_null(OsStr::new(FIXED_CHILD_CWD));
    let environment = wide_null(OsStr::new("SYSTEMROOT=C:\\Windows\0"));
    // SAFETY: zero is the documented initialization baseline.
    let mut startup: STARTUPINFOW = unsafe { std::mem::zeroed() };
    startup.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
    // SAFETY: zero is the documented output baseline.
    let mut process: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
    // SAFETY: every pointer remains live for the call; the fixed child has no
    // inherited handles, no console, a fixed environment, and a fixed cwd.
    let created = unsafe {
        CreateProcessW(
            application.as_ptr(),
            command_line.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            0,
            DETACHED_PROCESS | CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
            environment.as_ptr().cast(),
            current_directory.as_ptr(),
            &startup,
            &mut process,
        )
    };
    if created == 0 {
        // SAFETY: sampled immediately after failed CreateProcessW.
        let error = unsafe { GetLastError() };
        let blocked = error == ERROR_CHILD_PROCESS_BLOCKED;
        println!(
            "{{\"terminal\":\"{}\",\"child_creation_attempted\":true,\"child_creation_blocked\":{},\"win32_error\":{}}}",
            if blocked {
                "CHILD_CREATION_BLOCKED_BY_POLICY"
            } else {
                "CHILD_CREATION_FAILED_UNEXPECTEDLY"
            },
            if blocked { "true" } else { "false" },
            error,
        );
        return ExitCode::from(if blocked { 0 } else { 3 });
    }

    // The policy failed: contain the unexpected child before reporting.
    // SAFETY: CreateProcessW returned both handles on success.
    let _ = unsafe { TerminateProcess(process.hProcess, UNEXPECTED_CHILD_EXIT) };
    let wait = unsafe { WaitForSingleObject(process.hProcess, 5_000) };
    // SAFETY: both handles are uniquely owned and closed once.
    let _ = unsafe { CloseHandle(process.hThread) };
    let _ = unsafe { CloseHandle(process.hProcess) };
    if wait == WAIT_OBJECT_0 {
        println!(
            "{{\"terminal\":\"CHILD_CREATION_ALLOWED\",\"child_creation_attempted\":true,\"child_creation_blocked\":false,\"win32_error\":0}}"
        );
        ExitCode::from(3)
    } else {
        println!(
            "{{\"terminal\":\"UNEXPECTED_CHILD_DRAIN_FAILED\",\"child_creation_attempted\":true,\"child_creation_blocked\":false,\"win32_error\":0}}"
        );
        ExitCode::from(10)
    }
}
