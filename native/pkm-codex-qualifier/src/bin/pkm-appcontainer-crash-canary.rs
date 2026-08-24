use std::env;
use std::ffi::OsStr;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeleteAppContainerProfile, DeriveAppContainerSidFromAppContainerName,
};
use windows_sys::Win32::Security::{EqualSid, FreeSid, IsValidSid};
use windows_sys::Win32::Storage::FileSystem::{FlushFileBuffers, GetFileType, FILE_TYPE_PIPE};
use windows_sys::Win32::System::JobObjects::IsProcessInJob;
use windows_sys::Win32::System::SystemServices::PROCESS_MITIGATION_CHILD_PROCESS_POLICY;
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, GetProcessMitigationPolicy, ProcessChildProcessPolicy,
};

const RUN_ID_ENV: &str = "PKM_APPCONTAINER_CRASH_RUN_ID";
const MODE_ENV: &str = "PKM_APPCONTAINER_RECOVERY_MODE";
const KIND_ENV: &str = "PKM_APPCONTAINER_RUNTIME_KIND";
const SCRATCH_ROOT: &str = r"D:\personal\obsidian_workflow-runtime\scratch";

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
    let Ok(mode) = env::var(MODE_ENV) else {
        return ExitCode::from(3);
    };
    let Ok(kind) = env::var(KIND_ENV) else {
        return ExitCode::from(3);
    };
    let prefix = match kind.as_str() {
        "CREATOR_KILL" => "codex-t13b-appcontainer-creator-kill-",
        "OWNER_RESTART" => "codex-t13b-appcontainer-owner-restart-",
        _ => return ExitCode::from(3),
    };
    if (mode == "RECOVER" && kind != "OWNER_RESTART")
        || !matches!(mode.as_str(), "CREATE" | "RECOVER")
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
    let runtime_root = PathBuf::from(SCRATCH_ROOT).join(format!("{prefix}{run_id}"));
    if !runtime_root.is_dir() {
        return ExitCode::from(3);
    }
    match mode.as_str() {
        "CREATE" => create_and_wait(&run_id, &kind, &runtime_root, &mut stdout),
        "RECOVER" => recover_from_intent(&run_id, &runtime_root, &mut stdout),
        _ => ExitCode::from(3),
    }
}

fn create_and_wait(
    run_id: &str,
    kind: &str,
    runtime_root: &Path,
    stdout: &mut impl Write,
) -> ExitCode {
    let profile_name_text = format!("OpenAI.PKMWorkflow.Codex.T13b.{run_id}");
    if kind == "OWNER_RESTART" {
        let intent = format!(
            "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-intent.v1\",\"run_id\":\"{run_id}\",\"profile_name\":\"{profile_name_text}\",\"phase\":\"PREPARED_BEFORE_PROFILE\"}}\n"
        );
        if create_new_flushed_file(&runtime_root.join("create-intent.json"), intent.as_bytes())
            .is_err()
        {
            return ExitCode::from(3);
        }
    }
    let profile_name = wide_null(OsStr::new(&profile_name_text));
    let display_name = wide_null(OsStr::new("PKM Codex owner process canary"));
    let description = wide_null(OsStr::new(
        "Creates one zero-capability profile and waits for Job termination",
    ));
    let mut sid = std::ptr::null_mut();
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

fn recover_from_intent(run_id: &str, runtime_root: &Path, stdout: &mut impl Write) -> ExitCode {
    let profile_name_text = format!("OpenAI.PKMWorkflow.Codex.T13b.{run_id}");
    let expected_intent = format!(
        "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-intent.v1\",\"run_id\":\"{run_id}\",\"profile_name\":\"{profile_name_text}\",\"phase\":\"PREPARED_BEFORE_PROFILE\"}}\n"
    );
    let mut intent = Vec::new();
    let Ok(mut intent_file) = OpenOptions::new()
        .read(true)
        .open(runtime_root.join("create-intent.json"))
    else {
        return ExitCode::from(3);
    };
    if std::io::Read::by_ref(&mut intent_file)
        .take(4097)
        .read_to_end(&mut intent)
        .is_err()
        || intent != expected_intent.as_bytes()
    {
        return ExitCode::from(3);
    }
    let profile_name = wide_null(OsStr::new(&profile_name_text));
    let mut first_sid = std::ptr::null_mut();
    if unsafe { DeriveAppContainerSidFromAppContainerName(profile_name.as_ptr(), &mut first_sid) }
        < 0
        || first_sid.is_null()
        || unsafe { IsValidSid(first_sid) } == 0
    {
        if !first_sid.is_null() {
            let _ = unsafe { FreeSid(first_sid) };
        }
        return ExitCode::from(3);
    }
    if unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) } != 0 {
        let _ = unsafe { FreeSid(first_sid) };
        return ExitCode::from(3);
    }
    let display_name = wide_null(OsStr::new("PKM Codex fresh recovery canary"));
    let description = wide_null(OsStr::new(
        "Fresh process validates durable recovery intent",
    ));
    let mut second_sid = std::ptr::null_mut();
    let create_status = unsafe {
        CreateAppContainerProfile(
            profile_name.as_ptr(),
            display_name.as_ptr(),
            description.as_ptr(),
            std::ptr::null(),
            0,
            &mut second_sid,
        )
    };
    let stable = create_status == 0
        && !second_sid.is_null()
        && unsafe { IsValidSid(second_sid) } != 0
        && unsafe { EqualSid(first_sid, second_sid) } != 0;
    if !stable {
        let _ = unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) };
        let _ = unsafe { FreeSid(first_sid) };
        if !second_sid.is_null() {
            let _ = unsafe { FreeSid(second_sid) };
        }
        return ExitCode::from(3);
    }
    if unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) } != 0 {
        let _ = unsafe { FreeSid(first_sid) };
        let _ = unsafe { FreeSid(second_sid) };
        return ExitCode::from(3);
    }
    let _ = unsafe { FreeSid(first_sid) };
    let _ = unsafe { FreeSid(second_sid) };
    let recovery_receipt = format!(
        "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-phase-b.v1\",\"run_id\":\"{run_id}\",\"intent_verified\":true,\"stale_profile_deleted\":true,\"recreate\":true,\"sid_stable\":true,\"final_delete\":true}}\n"
    );
    if create_new_flushed_file(
        &runtime_root.join("phase-b-recovery-receipt.json"),
        recovery_receipt.as_bytes(),
    )
    .is_err()
    {
        return ExitCode::from(3);
    }
    if stdout
        .write_all(b"{\"terminal\":\"APPCONTAINER_OWNER_RESTART_RECOVERED\",\"intent_verified\":true,\"stale_profile_deleted\":true,\"recreate\":true,\"sid_stable\":true,\"final_delete\":true}\n")
        .and_then(|()| stdout.flush())
        .is_err()
    {
        return ExitCode::from(3);
    }
    ExitCode::SUCCESS
}

fn create_new_flushed_file(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.flush()?;
    if unsafe { FlushFileBuffers(file.as_raw_handle().cast()) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

fn wide_null(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(std::iter::once(0)).collect()
}
