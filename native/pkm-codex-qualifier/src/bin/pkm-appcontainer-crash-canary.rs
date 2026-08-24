use std::env;
use std::ffi::OsStr;
use std::io::Write;
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::AsRawHandle;
use std::process::ExitCode;
use std::time::Duration;

use windows_sys::Win32::Security::Isolation::CreateAppContainerProfile;
use windows_sys::Win32::Security::{FreeSid, IsValidSid};
use windows_sys::Win32::Storage::FileSystem::{GetFileType, FILE_TYPE_PIPE};
use windows_sys::Win32::System::JobObjects::IsProcessInJob;
use windows_sys::Win32::System::SystemServices::PROCESS_MITIGATION_CHILD_PROCESS_POLICY;
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, GetProcessMitigationPolicy, ProcessChildProcessPolicy,
};

const RUN_ID_ENV: &str = "PKM_APPCONTAINER_CRASH_RUN_ID";

fn main() -> ExitCode {
    if env::args_os().len() != 1 {
        return ExitCode::from(2);
    }
    let Ok(run_id) = env::var(RUN_ID_ENV) else {
        return ExitCode::from(3);
    };
    if run_id.len() != 32
        || !run_id
            .bytes()
            .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase())
    {
        return ExitCode::from(3);
    }
    let mut in_job = 0;
    // SAFETY: current-process pseudo handle is always live and output is writable.
    if unsafe { IsProcessInJob(GetCurrentProcess(), std::ptr::null_mut(), &mut in_job) } == 0
        || in_job == 0
    {
        return ExitCode::from(3);
    }
    // SAFETY: zero is the documented baseline for policy readback.
    let mut child_policy: PROCESS_MITIGATION_CHILD_PROCESS_POLICY = unsafe { std::mem::zeroed() };
    if unsafe {
        GetProcessMitigationPolicy(
            GetCurrentProcess(),
            ProcessChildProcessPolicy,
            (&mut child_policy as *mut PROCESS_MITIGATION_CHILD_PROCESS_POLICY).cast(),
            std::mem::size_of::<PROCESS_MITIGATION_CHILD_PROCESS_POLICY>(),
        )
    } == 0
        || unsafe { child_policy.Anonymous.Flags } != 1
    {
        return ExitCode::from(3);
    }
    let stdout = std::io::stdout();
    let mut stdout = stdout.lock();
    // SAFETY: stdout lock exposes the live inherited OS handle.
    if unsafe { GetFileType(stdout.as_raw_handle().cast()) } != FILE_TYPE_PIPE {
        return ExitCode::from(3);
    }
    let profile_name = format!("OpenAI.PKMWorkflow.Codex.T13b.{run_id}");
    let profile_name = wide_null(OsStr::new(&profile_name));
    let display_name = wide_null(OsStr::new("PKM Codex crash recovery canary"));
    let description = wide_null(OsStr::new(
        "Creates one zero-capability profile and waits for Job termination",
    ));
    let mut sid = std::ptr::null_mut();
    // SAFETY: strings are NUL-terminated, capability count is exactly zero,
    // and sid is writable. The parent owns reconciliation after hard kill.
    let status = unsafe {
        CreateAppContainerProfile(
            profile_name.as_ptr(),
            display_name.as_ptr(),
            description.as_ptr(),
            std::ptr::null(),
            0,
            &mut sid,
        )
    };
    if status < 0 || sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
        if !sid.is_null() {
            let _ = unsafe { FreeSid(sid) };
        }
        return ExitCode::from(3);
    }
    // SAFETY: CreateAppContainerProfile returns FreeSid-owned storage.
    let _ = unsafe { FreeSid(sid) };
    if stdout
        .write_all(
            b"{\"terminal\":\"APPCONTAINER_PROFILE_CREATED_WAITING_FOR_KILL\",\"profile_created\":true}\n",
        )
        .and_then(|()| stdout.flush())
        .is_err()
    {
        return ExitCode::from(3);
    }
    loop {
        std::thread::sleep(Duration::from_secs(60));
    }
}

fn wide_null(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(std::iter::once(0)).collect()
}
