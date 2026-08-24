use std::env;
use std::ffi::OsStr;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeleteAppContainerProfile, DeriveAppContainerSidFromAppContainerName,
};
use windows_sys::Win32::Security::{EqualSid, FreeSid, IsValidSid};
use windows_sys::Win32::Storage::FileSystem::{
    FlushFileBuffers, GetFileInformationByHandle, GetFileType, BY_HANDLE_FILE_INFORMATION,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_TYPE_PIPE,
};
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
    let Ok(mode) = env::var(MODE_ENV) else {
        return ExitCode::from(3);
    };
    let Ok(kind) = env::var(KIND_ENV) else {
        return ExitCode::from(3);
    };
    let run_id = env::var(RUN_ID_ENV).ok();
    if (mode == "RECOVER" && kind != "OWNER_RESTART")
        || !matches!(mode.as_str(), "CREATE" | "RECOVER")
        || (mode == "RECOVER" && run_id.is_some())
    {
        return ExitCode::from(3);
    }
    if mode == "CREATE" && !run_id.as_deref().is_some_and(valid_run_id) {
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
    match mode.as_str() {
        "CREATE" => {
            let Some(run_id) = run_id.as_deref() else {
                return ExitCode::from(3);
            };
            let prefix = match kind.as_str() {
                "CREATOR_KILL" => "codex-t13b-appcontainer-creator-kill-",
                "OWNER_RESTART" => "codex-t13b-appcontainer-owner-restart-",
                _ => return ExitCode::from(3),
            };
            let runtime_root = PathBuf::from(SCRATCH_ROOT).join(format!("{prefix}{run_id}"));
            if !runtime_root.is_dir() {
                return ExitCode::from(3);
            }
            create_and_wait(run_id, &kind, &runtime_root, &mut stdout)
        }
        "RECOVER" => recover_pending_intents(&mut stdout),
        _ => ExitCode::from(3),
    }
}

fn valid_run_id(run_id: &str) -> bool {
    run_id.len() == 32
        && run_id
            .bytes()
            .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase())
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

fn recover_pending_intents(stdout: &mut impl Write) -> ExitCode {
    let Ok(_scratch_pin) = open_pinned_directory(Path::new(SCRATCH_ROOT)) else {
        return ExitCode::from(3);
    };
    let Ok(entries) = std::fs::read_dir(SCRATCH_ROOT) else {
        return ExitCode::from(3);
    };
    let prefix = "codex-t13b-appcontainer-owner-restart-";
    let mut candidates = Vec::new();
    for entry in entries {
        let Ok(entry) = entry else {
            return ExitCode::from(3);
        };
        let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        let Some(run_id) = name.strip_prefix(prefix) else {
            continue;
        };
        if !valid_run_id(run_id) {
            continue;
        }
        if candidates.len() == 4096 {
            return ExitCode::from(3);
        }
        let run_id = run_id.to_owned();
        candidates.push((name, run_id, entry.path()));
    }
    candidates.sort_by(|left, right| left.0.cmp(&right.0));
    for (_, run_id, runtime_root) in candidates {
        let Ok(_candidate_pin) = open_pinned_directory(&runtime_root) else {
            return ExitCode::from(3);
        };
        let receipt_path = runtime_root.join("phase-b-recovery-receipt.json");
        let intent_path = runtime_root.join("create-intent.json");
        let intent_present = match intent_path.try_exists() {
            Ok(present) => present,
            Err(_) => return ExitCode::from(3),
        };
        let receipt_present = match receipt_path.try_exists() {
            Ok(present) => present,
            Err(_) => return ExitCode::from(3),
        };
        if receipt_present {
            if !intent_present {
                return ExitCode::from(3);
            }
            let Ok(intent) = read_bounded_regular_file(&intent_path, 4096) else {
                return ExitCode::from(3);
            };
            if intent != expected_intent(&run_id).as_bytes() {
                return ExitCode::from(3);
            }
            let Ok(receipt) = read_bounded_regular_file(&receipt_path, 4096) else {
                return ExitCode::from(3);
            };
            if !valid_completed_receipt(&run_id, &receipt) {
                return ExitCode::from(3);
            }
            match probe_profile_presence(&run_id) {
                Ok(false) => {}
                Ok(true) | Err(()) => return ExitCode::from(3),
            }
            continue;
        }
        if !intent_present {
            continue;
        }
        let mut recovery_output = Vec::new();
        let recovered = recover_from_intent(&run_id, &runtime_root, &mut recovery_output);
        let output = recovery_output.as_slice();
        let recovered_existing_profile = output
            == b"{\"terminal\":\"APPCONTAINER_OWNER_RESTART_RECOVERED\",\"intent_verified\":true,\"stale_profile_deleted\":true,\"recreate\":true,\"sid_stable\":true,\"final_delete\":true}\n";
        let reconciled_absent_profile = output
            == b"{\"terminal\":\"APPCONTAINER_OWNER_RESTART_NO_PROFILE_RECONCILED\",\"intent_verified\":true,\"profile_absent_at_recovery_entry\":true,\"probe_created_and_deleted\":true}\n";
        if recovered != ExitCode::SUCCESS
            || (!recovered_existing_profile && !reconciled_absent_profile)
        {
            return ExitCode::from(3);
        }
    }
    if stdout
        .write_all(b"{\"terminal\":\"APPCONTAINER_OWNER_RESTART_SCAN_COMPLETED\",\"scan_completed\":true}\n")
        .and_then(|()| stdout.flush())
        .is_err()
    {
        return ExitCode::from(3);
    }
    ExitCode::SUCCESS
}

fn expected_intent(run_id: &str) -> String {
    let profile_name = format!("OpenAI.PKMWorkflow.Codex.T13b.{run_id}");
    format!(
        "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-intent.v1\",\"run_id\":\"{run_id}\",\"profile_name\":\"{profile_name}\",\"phase\":\"PREPARED_BEFORE_PROFILE\"}}\n"
    )
}

fn valid_completed_receipt(run_id: &str, receipt: &[u8]) -> bool {
    let recovered_existing = format!(
        "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-phase-b.v1\",\"run_id\":\"{run_id}\",\"intent_verified\":true,\"stale_profile_deleted\":true,\"recreate\":true,\"sid_stable\":true,\"final_delete\":true}}\n"
    );
    let reconciled_absent = format!(
        "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-phase-b.v1\",\"run_id\":\"{run_id}\",\"intent_verified\":true,\"stale_profile_deleted\":false,\"profile_absent_at_recovery_entry\":true,\"probe_created_and_deleted\":true,\"recreate\":false,\"sid_stable\":null,\"final_delete\":true}}\n"
    );
    let legacy_reconciled_absent = format!(
        "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-phase-b.v1\",\"run_id\":\"{run_id}\",\"intent_verified\":true,\"stale_profile_deleted\":false,\"profile_absent_after_prepared_intent\":true,\"recreate\":false,\"sid_stable\":null,\"final_delete\":true}}\n"
    );
    receipt == recovered_existing.as_bytes()
        || receipt == reconciled_absent.as_bytes()
        || receipt == legacy_reconciled_absent.as_bytes()
}

fn recover_from_intent(run_id: &str, runtime_root: &Path, stdout: &mut impl Write) -> ExitCode {
    let profile_name_text = format!("OpenAI.PKMWorkflow.Codex.T13b.{run_id}");
    let expected_intent = expected_intent(run_id);
    let Ok(intent) = read_bounded_regular_file(&runtime_root.join("create-intent.json"), 4096)
    else {
        return ExitCode::from(3);
    };
    if intent != expected_intent.as_bytes() {
        return ExitCode::from(3);
    }
    match probe_profile_presence(run_id) {
        Ok(false) => {
            let recovery_receipt = format!(
                "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-phase-b.v1\",\"run_id\":\"{run_id}\",\"intent_verified\":true,\"stale_profile_deleted\":false,\"profile_absent_at_recovery_entry\":true,\"probe_created_and_deleted\":true,\"recreate\":false,\"sid_stable\":null,\"final_delete\":true}}\n"
            );
            if create_new_flushed_file(
                &runtime_root.join("phase-b-recovery-receipt.json"),
                recovery_receipt.as_bytes(),
            )
            .is_err()
                || stdout
                    .write_all(b"{\"terminal\":\"APPCONTAINER_OWNER_RESTART_NO_PROFILE_RECONCILED\",\"intent_verified\":true,\"profile_absent_at_recovery_entry\":true,\"probe_created_and_deleted\":true}\n")
                    .and_then(|()| stdout.flush())
                    .is_err()
            {
                return ExitCode::from(3);
            }
            return ExitCode::SUCCESS;
        }
        Ok(true) => {}
        Err(()) => return ExitCode::from(3),
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

fn probe_profile_presence(run_id: &str) -> Result<bool, ()> {
    let profile_name_text = format!("OpenAI.PKMWorkflow.Codex.T13b.{run_id}");
    let profile_name = wide_null(OsStr::new(&profile_name_text));
    let display_name = wide_null(OsStr::new("PKM Codex recovery presence probe"));
    let description = wide_null(OsStr::new(
        "Atomically distinguishes an absent profile from an existing profile",
    ));
    let mut sid = std::ptr::null_mut();
    // SAFETY: strings are NUL-terminated, capabilities are empty, and sid is writable.
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
    const HRESULT_ALREADY_EXISTS: i32 = 0x8007_00b7u32 as i32;
    if status == HRESULT_ALREADY_EXISTS {
        if !sid.is_null() {
            let _ = unsafe { FreeSid(sid) };
        }
        return Ok(true);
    }
    if status != 0 {
        if !sid.is_null() {
            let _ = unsafe { FreeSid(sid) };
        }
        return Err(());
    }
    if sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
        if !sid.is_null() {
            let _ = unsafe { FreeSid(sid) };
        }
        let _ = unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) };
        return Err(());
    }
    let _ = unsafe { FreeSid(sid) };
    if unsafe { DeleteAppContainerProfile(profile_name.as_ptr()) } != 0 {
        return Err(());
    }
    Ok(false)
}

fn open_pinned_directory(path: &Path) -> std::io::Result<std::fs::File> {
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_dir() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(std::io::Error::other(
            "runtime directory must be a non-reparse directory",
        ));
    }
    Ok(file)
}

fn read_bounded_regular_file(path: &Path, limit: u64) -> std::io::Result<Vec<u8>> {
    let mut file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    let metadata = file.metadata()?;
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    if !metadata.is_file()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || information.nNumberOfLinks != 1
        || metadata.file_size() > limit
    {
        return Err(std::io::Error::other(
            "artifact must be a bounded, single-link, non-reparse regular file",
        ));
    }
    let mut bytes = Vec::with_capacity(metadata.file_size() as usize);
    std::io::Read::by_ref(&mut file)
        .take(limit + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != metadata.file_size() {
        return Err(std::io::Error::other(
            "artifact length changed while pinned",
        ));
    }
    Ok(bytes)
}

fn wide_null(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(std::iter::once(0)).collect()
}

#[cfg(test)]
mod tests {
    use super::valid_completed_receipt;

    #[test]
    fn completed_receipt_requires_exact_schema_run_id_and_terminal_facts() {
        let run_id = "0123456789abcdef0123456789abcdef";
        let valid = format!(
            "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-phase-b.v1\",\"run_id\":\"{run_id}\",\"intent_verified\":true,\"stale_profile_deleted\":false,\"profile_absent_at_recovery_entry\":true,\"probe_created_and_deleted\":true,\"recreate\":false,\"sid_stable\":null,\"final_delete\":true}}\n"
        );
        let legacy = format!(
            "{{\"schema\":\"pkm.codex.appcontainer-owner-restart-phase-b.v1\",\"run_id\":\"{run_id}\",\"intent_verified\":true,\"stale_profile_deleted\":false,\"profile_absent_after_prepared_intent\":true,\"recreate\":false,\"sid_stable\":null,\"final_delete\":true}}\n"
        );
        assert!(valid_completed_receipt(run_id, valid.as_bytes()));
        assert!(valid_completed_receipt(run_id, legacy.as_bytes()));
        assert!(!valid_completed_receipt(run_id, b""));
        assert!(!valid_completed_receipt(
            run_id,
            &valid.as_bytes()[..valid.len() - 7]
        ));
        assert!(!valid_completed_receipt(
            run_id,
            valid
                .replace(
                    "appcontainer-owner-restart-phase-b.v1",
                    "appcontainer-owner-restart-phase-b.v2"
                )
                .as_bytes()
        ));
        assert!(!valid_completed_receipt(
            run_id,
            valid
                .replace("final_delete\":true", "final_delete\":false")
                .as_bytes()
        ));
        assert!(!valid_completed_receipt(
            run_id,
            valid
                .replace(
                    "\"final_delete\":true",
                    "\"unknown\":true,\"final_delete\":true"
                )
                .as_bytes()
        ));
        assert!(!valid_completed_receipt(
            "fedcba9876543210fedcba9876543210",
            valid.as_bytes()
        ));
    }
}
