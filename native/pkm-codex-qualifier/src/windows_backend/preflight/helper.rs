use super::*;
use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};

use windows_sys::Win32::Foundation::{
    DBG_CONTINUE, DBG_EXCEPTION_NOT_HANDLED, ERROR_HANDLE_EOF, ERROR_IO_PENDING,
    ERROR_NOTIFY_ENUM_DIR, ERROR_NOT_FOUND, ERROR_OPERATION_ABORTED, ERROR_SEM_TIMEOUT,
    EXCEPTION_BREAKPOINT, FILETIME, WAIT_TIMEOUT,
};
use windows_sys::Win32::Storage::FileSystem::{
    FindClose, FindFirstStreamW, FindNextStreamW, FindStreamInfoStandard, ReadDirectoryChangesW,
    FILE_ACTION_ADDED, FILE_ACTION_MODIFIED, FILE_ACTION_REMOVED, FILE_ACTION_RENAMED_NEW_NAME,
    FILE_ACTION_RENAMED_OLD_NAME, FILE_FLAG_OVERLAPPED, FILE_LIST_DIRECTORY,
    FILE_NOTIFY_CHANGE_ATTRIBUTES, FILE_NOTIFY_CHANGE_CREATION, FILE_NOTIFY_CHANGE_DIR_NAME,
    FILE_NOTIFY_CHANGE_FILE_NAME, FILE_NOTIFY_CHANGE_LAST_WRITE, FILE_NOTIFY_CHANGE_SIZE,
    WIN32_FIND_STREAM_DATA,
};
use windows_sys::Win32::System::Diagnostics::Debug::{
    ContinueDebugEvent, DebugSetProcessKillOnExit, WaitForDebugEvent, CREATE_PROCESS_DEBUG_EVENT,
    CREATE_THREAD_DEBUG_EVENT, DEBUG_EVENT, EXCEPTION_DEBUG_EVENT, EXIT_PROCESS_DEBUG_EVENT,
    EXIT_THREAD_DEBUG_EVENT, LOAD_DLL_DEBUG_EVENT, OUTPUT_DEBUG_STRING_EVENT, RIP_EVENT,
    UNLOAD_DLL_DEBUG_EVENT,
};
use windows_sys::Win32::System::JobObjects::{
    IsProcessInJob, JobObjectAssociateCompletionPortInformation, JobObjectExtendedLimitInformation,
    QueryInformationJobObject, SetInformationJobObject, JOBOBJECT_ASSOCIATE_COMPLETION_PORT,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
    JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
};
use windows_sys::Win32::System::SystemServices::{
    JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT, JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO,
    JOB_OBJECT_MSG_EXIT_PROCESS, JOB_OBJECT_MSG_NEW_PROCESS,
};
use windows_sys::Win32::System::Threading::{
    CreateEventW, GetCurrentProcessId, GetProcessId, GetProcessTimes, QueryFullProcessImageNameW,
    ResetEvent, DEBUG_PROCESS, DETACHED_PROCESS,
};
use windows_sys::Win32::System::IO::{
    CancelIoEx, CreateIoCompletionPort, GetOverlappedResultEx, GetQueuedCompletionStatus,
    OVERLAPPED,
};

const PROCESS_BASIC_INFORMATION_CLASS: u32 = 0;
const PROCESS_COMMAND_LINE_INFORMATION_CLASS: u32 = 60;
const MAX_COMMAND_LINE_BYTES: usize = 64 * 1024;
const DIRECTORY_CHANGE_BUFFER_BYTES: usize = 64 * 1024;
const FILE_NOTIFY_CHANGE_STREAM_NAME: u32 = 0x0000_0200;
const FILE_NOTIFY_CHANGE_STREAM_SIZE: u32 = 0x0000_0400;
const FILE_NOTIFY_CHANGE_STREAM_WRITE: u32 = 0x0000_0800;
const MAX_HOME_CHANGE_EVENTS: usize = 4_096;
const MAX_HOME_INVENTORY_ENTRIES: usize = 1_024;
const HELPER_DEADLINE: Duration = Duration::from_secs(10);

#[repr(C)]
struct NativeProcessBasicInformation {
    reserved1: *mut core::ffi::c_void,
    peb_base_address: *mut core::ffi::c_void,
    reserved2: [*mut core::ffi::c_void; 2],
    unique_process_id: usize,
    inherited_from_unique_process_id: usize,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct NativeUnicodeString {
    length: u16,
    maximum_length: u16,
    buffer: *const u16,
}

#[link(name = "ntdll")]
extern "system" {
    fn NtQueryInformationProcess(
        process_handle: HANDLE,
        process_information_class: u32,
        process_information: *mut core::ffi::c_void,
        process_information_length: u32,
        return_length: *mut u32,
    ) -> i32;
}

const HELPER_VARIANT_PREFIXES: [&str; 4] = [
    "codex-t13b-helper-b-",
    "codex-t13b-helper-d-",
    "codex-t13b-helper-e-",
    "codex-t13b-helper-f-",
];
const HELPER_SUMMARY_PREFIX: &str = "codex-t13b-helper-summary-";
const HELPER_TERMINAL: &str = "BLOCKED_HELPER_DIAGNOSTIC";
const HELPER_NEVER_QUALIFIES: &str = "HELPER_DIAGNOSTIC_ONLY_NEVER_QUALIFIES";
const HELPER_ETW_BLOCKER: &str = "FILE_ACCESS_ETW_TRACE_REQUIRED";
const HELPER_CONHOST_BLOCKER: &str = "OBSERVED_NON_CODEX_CONHOST_DESCENDANT";
const HELPER_JOB_LIMIT_OBSERVATION_BLOCKER: &str =
    "JOB_ACTIVE_PROCESS_LIMIT_ONE_OBSERVED_WITH_TWO_JOB_PROCESSES";
const HELPER_COMMAND_LINE_SOURCE: &str = "NTQUERY_INFORMATION_PROCESS_COMMAND_LINE";
const HELPER_CODEX_IMAGE_ROLE: &str = "PINNED_CODEX_ROOT";
const HELPER_CONHOST_IMAGE_ROLE: &str = "FIXED_CONTENT_CONHOST_DESCENDANT";
const HELPER_UNEXPECTED_IMAGE_ROLE: &str = "UNEXPECTED_PROCESS_IMAGE";
const FIXED_CONHOST_PATH: &str = r"C:\Windows\System32\conhost.exe";
const FIXED_CONHOST_COMMAND_LINE: &str = r"\??\C:\WINDOWS\system32\conhost.exe 0x4";
const FIXED_CONHOST_LENGTH: u64 = 1_003_520;
const FIXED_CONHOST_SHA256: &str =
    "sha256:7ced551ec8afab3391a3ede5442046558e4799dcfd631ac9252fe93929a3465e";

const HELPER_POLICIES: [LaunchPolicy; 4] = [
    LaunchPolicy {
        id: "B",
        atomic_job_list: true,
        child_process_restricted: false,
        assigned_after_create: false,
    },
    LaunchPolicy {
        id: "D",
        atomic_job_list: false,
        child_process_restricted: false,
        assigned_after_create: true,
    },
    LaunchPolicy {
        id: "E",
        atomic_job_list: true,
        child_process_restricted: false,
        assigned_after_create: false,
    },
    LaunchPolicy {
        id: "F",
        atomic_job_list: true,
        child_process_restricted: true,
        assigned_after_create: false,
    },
];

fn helper_console_creation_flag(policy: LaunchPolicy) -> Result<u32, Blocker> {
    match policy.id {
        "B" | "D" => Ok(CREATE_NO_WINDOW),
        "E" | "F" => Ok(DETACHED_PROCESS),
        _ => Err(Blocker::new(
            Gate::Manifest,
            "HELPER_CONSOLE_CREATION_MODE_INVALID",
        )),
    }
}

fn helper_console_creation_mode(policy: LaunchPolicy) -> Result<&'static str, Blocker> {
    match policy.id {
        "B" | "D" => Ok("CREATE_NO_WINDOW"),
        "E" | "F" => Ok("DETACHED_PROCESS"),
        _ => Err(Blocker::new(
            Gate::Manifest,
            "HELPER_CONSOLE_CREATION_MODE_INVALID",
        )),
    }
}

#[derive(Debug)]
struct JobCompletionMonitor {
    port: OwnedKernelHandle,
    completion_key: Box<u8>,
    events: Vec<NativeHelperJobEvent>,
    readback_complete: bool,
    active_process_limit: u32,
    limit_flags: u32,
}

impl JobCompletionMonitor {
    fn associate(job: &OwnedKernelHandle) -> Result<Self, Blocker> {
        // SAFETY: INVALID_HANDLE_VALUE with a null existing port creates a new
        // private completion port. The returned handle is owned here.
        let port =
            unsafe { CreateIoCompletionPort(INVALID_HANDLE_VALUE, std::ptr::null_mut(), 0, 1) };
        if port.is_null() {
            return Err(Blocker::new(
                Gate::JobPrepared,
                "HELPER_JOB_COMPLETION_PORT_CREATE_FAILED",
            ));
        }
        let port = OwnedKernelHandle(port);
        let completion_key = Box::new(0u8);
        let association = JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
            CompletionKey: completion_key.as_ref() as *const u8 as *mut core::ffi::c_void,
            CompletionPort: port.0,
        };
        // SAFETY: job, port, and the exact typed input structure are live.
        if unsafe {
            SetInformationJobObject(
                job.0,
                JobObjectAssociateCompletionPortInformation,
                (&association as *const JOBOBJECT_ASSOCIATE_COMPLETION_PORT).cast(),
                std::mem::size_of::<JOBOBJECT_ASSOCIATE_COMPLETION_PORT>() as u32,
            )
        } == 0
        {
            return Err(Blocker::new(
                Gate::JobPrepared,
                "HELPER_JOB_COMPLETION_PORT_ASSOCIATE_FAILED",
            ));
        }

        // JobObjectAssociateCompletionPortInformation is set-only on the
        // qualified Windows build. The operational readback is therefore the
        // exact completion key on NEW/EXIT/ACTIVE_ZERO messages; no bool is
        // accepted in its place. Limits remain directly queryable below.
        // SAFETY: exact-size readback for the already configured Job limits.
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        let mut returned = 0u32;
        if unsafe {
            QueryInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                (&mut limits as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                &mut returned,
            )
        } == 0
            || returned != std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32
        {
            return Err(Blocker::new(
                Gate::JobPrepared,
                "HELPER_JOB_LIMIT_READBACK_FAILED",
            ));
        }
        let flags = limits.BasicLimitInformation.LimitFlags;
        if flags & (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
            != (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
            || flags & (JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK) != 0
            || limits.BasicLimitInformation.ActiveProcessLimit != 1
        {
            return Err(Blocker::new(
                Gate::JobPrepared,
                "HELPER_JOB_LIMIT_READBACK_MISMATCH",
            ));
        }
        Ok(Self {
            port,
            completion_key,
            events: Vec::new(),
            readback_complete: true,
            active_process_limit: limits.BasicLimitInformation.ActiveProcessLimit,
            limit_flags: flags,
        })
    }

    fn drain_nonblocking(&mut self) -> Result<(), Blocker> {
        loop {
            let mut message = 0u32;
            let mut completion_key = 0usize;
            let mut process_value: *mut OVERLAPPED = std::ptr::null_mut();
            // SAFETY: the private port and all output pointers are live. A
            // zero timeout makes this a bounded queue drain.
            let ok = unsafe {
                GetQueuedCompletionStatus(
                    self.port.0,
                    &mut message,
                    &mut completion_key,
                    &mut process_value,
                    0,
                )
            };
            if ok == 0 {
                // SAFETY: sampled immediately after the failed queue call.
                let error = unsafe { GetLastError() };
                if error == WAIT_TIMEOUT {
                    return Ok(());
                }
                return Err(Blocker::new(
                    Gate::JobDrained,
                    "HELPER_JOB_COMPLETION_PORT_DRAIN_FAILED",
                ));
            }
            let expected_key = self.completion_key.as_ref() as *const u8 as usize;
            if completion_key != expected_key {
                return Err(Blocker::new(
                    Gate::JobDrained,
                    "HELPER_JOB_COMPLETION_KEY_MISMATCH",
                ));
            }
            let (kind, process_id) = match message {
                JOB_OBJECT_MSG_NEW_PROCESS => (
                    "NEW_PROCESS",
                    process_id_from_completion_value(process_value)?,
                ),
                JOB_OBJECT_MSG_EXIT_PROCESS => (
                    "EXIT_PROCESS",
                    process_id_from_completion_value(process_value)?,
                ),
                JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO => ("ACTIVE_PROCESS_ZERO", None),
                JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT => ("ACTIVE_PROCESS_LIMIT", None),
                _ => {
                    return Err(Blocker::new(
                        Gate::JobDrained,
                        "HELPER_JOB_COMPLETION_MESSAGE_UNKNOWN",
                    ));
                }
            };
            let sequence = self.events.len().try_into().map_err(|_| {
                Blocker::new(Gate::JobDrained, "HELPER_JOB_EVENT_SEQUENCE_OVERFLOW")
            })?;
            self.events.push(NativeHelperJobEvent {
                sequence,
                kind,
                process_id,
            });
            if self.events.len() > 64 {
                return Err(Blocker::new(
                    Gate::JobDrained,
                    "HELPER_JOB_EVENT_LIMIT_EXCEEDED",
                ));
            }
        }
    }
}

fn process_id_from_completion_value(value: *mut OVERLAPPED) -> Result<Option<u32>, Blocker> {
    let raw = value as usize;
    if raw == 0 || raw > u32::MAX as usize {
        return Err(Blocker::new(
            Gate::JobDrained,
            "HELPER_JOB_EVENT_PROCESS_ID_INVALID",
        ));
    }
    Ok(Some(raw as u32))
}

#[derive(Debug)]
struct DirectoryWatcher {
    stop: Arc<AtomicBool>,
    result: mpsc::Receiver<Result<Vec<NativeHelperHomeChangeEvent>, Blocker>>,
    thread: Option<JoinHandle<()>>,
}

impl DirectoryWatcher {
    fn start(path: &Path) -> Result<Self, Blocker> {
        let directory = OpenOptions::new()
            .access_mode(FILE_LIST_DIRECTORY)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(
                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_OVERLAPPED,
            )
            .open(path)
            .map_err(|_| {
                Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_WATCH_DIRECTORY_OPEN_FAILED",
                )
            })?;
        if directory
            .metadata()
            .map_err(|_| {
                Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_WATCH_DIRECTORY_METADATA_FAILED",
                )
            })?
            .file_attributes()
            & FILE_ATTRIBUTE_REPARSE_POINT
            != 0
        {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_WATCH_REPARSE_POINT_FORBIDDEN",
            ));
        }
        let stop = Arc::new(AtomicBool::new(false));
        let thread_stop = Arc::clone(&stop);
        let (ready_tx, ready_rx) = mpsc::sync_channel(1);
        let (result_tx, result_rx) = mpsc::sync_channel(1);
        let thread = thread::Builder::new()
            .name("pkm-helper-home-watch".to_owned())
            .spawn(move || {
                let result = run_directory_watcher(directory, thread_stop, ready_tx);
                let _ = result_tx.send(result);
            })
            .map_err(|_| {
                Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_THREAD_START_FAILED")
            })?;
        match ready_rx.recv_timeout(Duration::from_secs(2)) {
            Ok(Ok(())) => Ok(Self {
                stop,
                result: result_rx,
                thread: Some(thread),
            }),
            Ok(Err(error)) => {
                let _ = thread.join();
                Err(error)
            }
            Err(_) => {
                stop.store(true, Ordering::Release);
                let _ = thread.join();
                Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_WATCH_START_TIMEOUT",
                ))
            }
        }
    }

    fn stop_and_collect(mut self) -> Result<Vec<NativeHelperHomeChangeEvent>, Blocker> {
        self.stop.store(true, Ordering::Release);
        let result = self
            .result
            .recv_timeout(Duration::from_secs(3))
            .map_err(|_| Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_STOP_TIMEOUT"))?;
        if let Some(thread) = self.thread.take() {
            thread.join().map_err(|_| {
                Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_THREAD_JOIN_FAILED")
            })?;
        }
        result
    }
}

impl Drop for DirectoryWatcher {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        // A failed watcher is diagnostic evidence loss. Never turn Drop into
        // an unbounded wait; dropping JoinHandle detaches the bounded worker.
        self.thread.take();
    }
}

fn run_directory_watcher(
    directory: File,
    stop: Arc<AtomicBool>,
    ready: mpsc::SyncSender<Result<(), Blocker>>,
) -> Result<Vec<NativeHelperHomeChangeEvent>, Blocker> {
    // SAFETY: null security/name creates a private manual-reset event.
    let event = unsafe { CreateEventW(std::ptr::null(), 1, 0, std::ptr::null()) };
    if event.is_null() {
        let blocker = Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_EVENT_CREATE_FAILED");
        let _ = ready.send(Err(blocker.clone()));
        return Err(blocker);
    }
    let event = OwnedKernelHandle(event);
    let mut buffer = vec![0u8; DIRECTORY_CHANGE_BUFFER_BYTES];
    let mut events = Vec::new();
    let mut first_request = true;
    loop {
        // SAFETY: event is a live manual-reset event.
        if unsafe { ResetEvent(event.0) } == 0 {
            let blocker = Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_EVENT_RESET_FAILED");
            if first_request {
                let _ = ready.send(Err(blocker.clone()));
            }
            return Err(blocker);
        }
        // SAFETY: zero is the documented OVERLAPPED baseline.
        let mut overlapped: OVERLAPPED = unsafe { std::mem::zeroed() };
        overlapped.hEvent = event.0;
        let notify_filter = FILE_NOTIFY_CHANGE_FILE_NAME
            | FILE_NOTIFY_CHANGE_DIR_NAME
            | FILE_NOTIFY_CHANGE_ATTRIBUTES
            | FILE_NOTIFY_CHANGE_SIZE
            | FILE_NOTIFY_CHANGE_LAST_WRITE
            | FILE_NOTIFY_CHANGE_CREATION
            | FILE_NOTIFY_CHANGE_STREAM_NAME
            | FILE_NOTIFY_CHANGE_STREAM_SIZE
            | FILE_NOTIFY_CHANGE_STREAM_WRITE;
        // SAFETY: directory is opened for overlapped directory notification;
        // buffer and OVERLAPPED remain live until completion/cancellation.
        let issued = unsafe {
            ReadDirectoryChangesW(
                directory.as_raw_handle().cast(),
                buffer.as_mut_ptr().cast(),
                buffer.len() as u32,
                1,
                notify_filter,
                std::ptr::null_mut(),
                &mut overlapped,
                None,
            )
        };
        if issued == 0 {
            // SAFETY: sampled immediately after the failed issue call.
            let error = unsafe { GetLastError() };
            if error != ERROR_IO_PENDING {
                let blocker = if error == ERROR_NOTIFY_ENUM_DIR {
                    Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_OVERFLOW")
                } else {
                    Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_ISSUE_FAILED")
                };
                if first_request {
                    let _ = ready.send(Err(blocker.clone()));
                }
                return Err(blocker);
            }
        }
        if first_request {
            ready.send(Ok(())).map_err(|_| {
                Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_READY_CHANNEL_CLOSED")
            })?;
            first_request = false;
        }

        loop {
            let mut transferred = 0u32;
            // SAFETY: file, OVERLAPPED and output stay live for this bounded wait.
            let completed = unsafe {
                GetOverlappedResultEx(
                    directory.as_raw_handle().cast(),
                    &overlapped,
                    &mut transferred,
                    50,
                    0,
                )
            };
            if completed != 0 {
                parse_directory_changes(&buffer[..transferred as usize], &mut events)?;
                if directory_watcher_should_finish_after_completion(stop.load(Ordering::Acquire)) {
                    validate_home_change_trace(&events)?;
                    return Ok(events);
                }
                break;
            }
            // SAFETY: sampled immediately after GetOverlappedResultEx.
            let error = unsafe { GetLastError() };
            if is_directory_watch_poll_timeout(error) && !stop.load(Ordering::Acquire) {
                continue;
            }
            if error == ERROR_NOTIFY_ENUM_DIR {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_WATCH_OVERFLOW",
                ));
            }
            if !is_directory_watch_poll_timeout(error) {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_WATCH_COMPLETION_FAILED",
                ));
            }

            // Stop is observed only after the child tree is proven drained.
            // SAFETY: the exact pending OVERLAPPED belongs to this handle.
            let cancelled = unsafe { CancelIoEx(directory.as_raw_handle().cast(), &overlapped) };
            if cancelled == 0 {
                // SAFETY: sampled immediately after cancellation.
                let cancel_error = unsafe { GetLastError() };
                if cancel_error != ERROR_NOT_FOUND {
                    return Err(Blocker::new(
                        Gate::FileBoundary,
                        "HELPER_HOME_WATCH_CANCEL_FAILED",
                    ));
                }
            }
            transferred = 0;
            // SAFETY: wait for the cancellation or a racing completion.
            let final_completion = unsafe {
                GetOverlappedResultEx(
                    directory.as_raw_handle().cast(),
                    &overlapped,
                    &mut transferred,
                    1_000,
                    0,
                )
            };
            if final_completion != 0 {
                parse_directory_changes(&buffer[..transferred as usize], &mut events)?;
            } else {
                // SAFETY: sampled immediately after the final wait.
                let final_error = unsafe { GetLastError() };
                if final_error != ERROR_OPERATION_ABORTED {
                    return Err(Blocker::new(
                        Gate::FileBoundary,
                        "HELPER_HOME_WATCH_CANCEL_DRAIN_FAILED",
                    ));
                }
            }
            validate_home_change_trace(&events)?;
            return Ok(events);
        }
    }
}

fn is_directory_watch_poll_timeout(error: u32) -> bool {
    // GetOverlappedResultEx uses the wait-family timeout code, WAIT_TIMEOUT
    // (258), not ERROR_TIMEOUT (1460). Keeping this exact prevents unrelated
    // Win32 failures from being silently treated as benign polling.
    error == WAIT_TIMEOUT
}

fn directory_watcher_should_finish_after_completion(stop_requested: bool) -> bool {
    // The stop bit is only set after Job drain. A notification may complete
    // concurrently with that transition; returning here prevents an
    // unnecessary new ReadDirectoryChangesW request and a detached watcher.
    stop_requested
}

fn parse_directory_changes(
    bytes: &[u8],
    events: &mut Vec<NativeHelperHomeChangeEvent>,
) -> Result<(), Blocker> {
    if bytes.is_empty() {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "HELPER_HOME_WATCH_ZERO_BYTE_COMPLETION",
        ));
    }
    let mut offset = 0usize;
    loop {
        if bytes.len().saturating_sub(offset) < 12 {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_WATCH_RECORD_TRUNCATED",
            ));
        }
        // SAFETY: explicit bounds above; unaligned reads match the packed wire layout.
        let next = unsafe { std::ptr::read_unaligned(bytes.as_ptr().add(offset).cast::<u32>()) };
        // SAFETY: same bounded record header.
        let action =
            unsafe { std::ptr::read_unaligned(bytes.as_ptr().add(offset + 4).cast::<u32>()) };
        // SAFETY: same bounded record header.
        let name_bytes =
            unsafe { std::ptr::read_unaligned(bytes.as_ptr().add(offset + 8).cast::<u32>()) }
                as usize;
        if name_bytes == 0
            || name_bytes % 2 != 0
            || name_bytes > bytes.len().saturating_sub(offset + 12)
        {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_WATCH_NAME_LENGTH_INVALID",
            ));
        }
        let mut wide_name = Vec::with_capacity(name_bytes / 2);
        for index in 0..(name_bytes / 2) {
            // SAFETY: exact name byte bounds were checked above.
            wide_name.push(unsafe {
                std::ptr::read_unaligned(bytes.as_ptr().add(offset + 12 + index * 2).cast::<u16>())
            });
        }
        let relative_path = String::from_utf16(&wide_name)
            .map_err(|_| Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_NAME_NOT_UTF16"))?;
        let path = Path::new(&relative_path);
        if relative_path.contains(['\0', ':'])
            || path.is_absolute()
            || path.components().any(|component| {
                matches!(
                    component,
                    Component::Prefix(_)
                        | Component::RootDir
                        | Component::CurDir
                        | Component::ParentDir
                )
            })
        {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_WATCH_PATH_INVALID",
            ));
        }
        let action = match action {
            FILE_ACTION_ADDED => "ADDED",
            FILE_ACTION_REMOVED => "REMOVED",
            FILE_ACTION_MODIFIED => "MODIFIED",
            FILE_ACTION_RENAMED_OLD_NAME => "RENAMED_OLD_NAME",
            FILE_ACTION_RENAMED_NEW_NAME => "RENAMED_NEW_NAME",
            _ => {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_WATCH_ACTION_UNKNOWN",
                ));
            }
        };
        let sequence = events
            .len()
            .try_into()
            .map_err(|_| Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_SEQUENCE_OVERFLOW"))?;
        events.push(NativeHelperHomeChangeEvent {
            sequence,
            action,
            relative_path,
        });
        if events.len() > MAX_HOME_CHANGE_EVENTS {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_WATCH_EVENT_LIMIT_EXCEEDED",
            ));
        }
        if next == 0 {
            return Ok(());
        }
        let next = next as usize;
        if next < 12 + name_bytes || next % 4 != 0 || next > bytes.len().saturating_sub(offset) {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_WATCH_NEXT_OFFSET_INVALID",
            ));
        }
        offset = offset
            .checked_add(next)
            .ok_or_else(|| Blocker::new(Gate::FileBoundary, "HELPER_HOME_WATCH_OFFSET_OVERFLOW"))?;
    }
}

fn inventory_codex_home(path: &Path) -> Result<Vec<NativeHelperHomeEntry>, Blocker> {
    let root = open_directory(path, Gate::FileBoundary)?;
    let (root_volume, root_file_id, _, _) = file_id_from_handle(&root, Gate::FileBoundary)?;
    let mut stack = vec![(path.to_path_buf(), root_file_id)];
    let mut inventory = Vec::new();
    while let Some((directory_path, directory_file_id)) = stack.pop() {
        let mut children = std::fs::read_dir(&directory_path)
            .map_err(|_| Blocker::new(Gate::FileBoundary, "HELPER_HOME_INVENTORY_READ_DIR_FAILED"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| {
                Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_INVENTORY_READ_ENTRY_FAILED",
                )
            })?;
        children.sort_by_key(std::fs::DirEntry::file_name);
        for child in children {
            let child_path = child.path();
            let relative = child_path.strip_prefix(path).map_err(|_| {
                Blocker::new(Gate::FileBoundary, "HELPER_HOME_INVENTORY_PATH_ESCAPE")
            })?;
            let relative_path = relative
                .to_str()
                .ok_or_else(|| {
                    Blocker::new(Gate::FileBoundary, "HELPER_HOME_INVENTORY_PATH_NOT_UTF8")
                })?
                .to_owned();
            if relative_path.is_empty()
                || relative_path.contains(':')
                || relative.components().any(|component| {
                    matches!(
                        component,
                        Component::Prefix(_)
                            | Component::RootDir
                            | Component::CurDir
                            | Component::ParentDir
                    )
                })
            {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_INVENTORY_PATH_INVALID",
                ));
            }
            let metadata = std::fs::symlink_metadata(&child_path).map_err(|_| {
                Blocker::new(Gate::FileBoundary, "HELPER_HOME_INVENTORY_METADATA_FAILED")
            })?;
            let reparse_point = metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0;
            if reparse_point {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_INVENTORY_REPARSE_POINT_FORBIDDEN",
                ));
            }
            let (kind, file, sha256) = if metadata.is_dir() {
                let directory = open_directory(&child_path, Gate::FileBoundary)?;
                ("directory", directory, None)
            } else if metadata.is_file() {
                let file = OpenOptions::new()
                    .read(true)
                    .share_mode(FILE_SHARE_READ)
                    .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
                    .open(&child_path)
                    .map_err(|_| {
                        Blocker::new(Gate::FileBoundary, "HELPER_HOME_INVENTORY_FILE_OPEN_FAILED")
                    })?;
                verify_only_default_data_stream(&child_path)?;
                let sha256 = sha256_open_file(&file, Gate::FileBoundary)?;
                ("file", file, Some(sha256))
            } else {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_INVENTORY_SPECIAL_FILE_FORBIDDEN",
                ));
            };
            let (volume_serial, file_id, link_count, length) =
                file_id_from_handle(&file, Gate::FileBoundary)?;
            let handle_attributes = file
                .metadata()
                .map_err(|_| {
                    Blocker::new(
                        Gate::FileBoundary,
                        "HELPER_HOME_INVENTORY_HANDLE_METADATA_FAILED",
                    )
                })?
                .file_attributes();
            if handle_attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
                || volume_serial != root_volume
                || (kind == "file" && link_count != 1)
            {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_INVENTORY_IDENTITY_INVALID",
                ));
            }
            inventory.push(NativeHelperHomeEntry {
                relative_path,
                kind,
                reparse_point,
                volume_serial,
                file_id: hex_bytes(&file_id),
                parent_file_id: hex_bytes(&directory_file_id),
                link_count,
                length,
                sha256,
            });
            if kind == "directory" {
                stack.push((child_path, file_id));
            }
            if inventory.len() > MAX_HOME_INVENTORY_ENTRIES {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_INVENTORY_ENTRY_LIMIT_EXCEEDED",
                ));
            }
        }
    }
    inventory.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    let mut paths = BTreeSet::new();
    let mut identities = BTreeSet::new();
    if inventory.iter().any(|entry| {
        !paths.insert(entry.relative_path.clone())
            || !identities.insert((entry.volume_serial, entry.file_id.clone()))
    }) {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "HELPER_HOME_INVENTORY_DUPLICATE_PATH_OR_IDENTITY",
        ));
    }
    Ok(inventory)
}

fn verify_only_default_data_stream(path: &Path) -> Result<(), Blocker> {
    let wide = wide_null(path.as_os_str());
    // SAFETY: zero is the output baseline.
    let mut data: WIN32_FIND_STREAM_DATA = unsafe { std::mem::zeroed() };
    // SAFETY: path is NUL-terminated and output is exact writable storage.
    let find = unsafe {
        FindFirstStreamW(
            wide.as_ptr(),
            FindStreamInfoStandard,
            (&mut data as *mut WIN32_FIND_STREAM_DATA).cast(),
            0,
        )
    };
    if find == INVALID_HANDLE_VALUE {
        // SAFETY: sampled immediately after FindFirstStreamW.
        let error = unsafe { GetLastError() };
        return Err(Blocker::new(
            Gate::FileBoundary,
            if error == ERROR_HANDLE_EOF {
                "HELPER_HOME_DEFAULT_STREAM_MISSING"
            } else {
                "HELPER_HOME_STREAM_ENUMERATION_FAILED"
            },
        ));
    }
    let find = OwnedFindHandle(find);
    let mut stream_count = 0usize;
    loop {
        let end = data
            .cStreamName
            .iter()
            .position(|value| *value == 0)
            .ok_or_else(|| {
                Blocker::new(Gate::FileBoundary, "HELPER_HOME_STREAM_NAME_UNTERMINATED")
            })?;
        let name = String::from_utf16(&data.cStreamName[..end])
            .map_err(|_| Blocker::new(Gate::FileBoundary, "HELPER_HOME_STREAM_NAME_NOT_UTF16"))?;
        stream_count += 1;
        if name != "::$DATA" || stream_count > 1 {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_ALTERNATE_DATA_STREAM_FORBIDDEN",
            ));
        }
        // SAFETY: find handle and output remain live.
        if unsafe { FindNextStreamW(find.0, (&mut data as *mut WIN32_FIND_STREAM_DATA).cast()) }
            == 0
        {
            // SAFETY: sampled immediately after FindNextStreamW.
            let error = unsafe { GetLastError() };
            if error == ERROR_HANDLE_EOF {
                break;
            }
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_STREAM_ENUMERATION_FAILED",
            ));
        }
    }
    if stream_count != 1 {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "HELPER_HOME_DEFAULT_STREAM_MISSING",
        ));
    }
    Ok(())
}

struct OwnedFindHandle(HANDLE);

impl Drop for OwnedFindHandle {
    fn drop(&mut self) {
        if self.0 != INVALID_HANDLE_VALUE {
            // SAFETY: owned stream enumeration handle is closed once.
            let _ = unsafe { FindClose(self.0) };
        }
    }
}

fn capture_process_evidence(
    process_handle: HANDLE,
    image_file: &File,
    event_process_id: u32,
    job: &OwnedKernelHandle,
    binary: &PinnedBinary,
    conhost: &PinnedConhostImage,
) -> Result<NativeHelperProcessEvidence, Blocker> {
    // SAFETY: process_handle is supplied by a CREATE_PROCESS_DEBUG_EVENT and
    // stays live for the duration of this capture.
    let process_id = unsafe { GetProcessId(process_handle) };
    if process_id == 0 || process_id != event_process_id {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DEBUG_EVENT_PROCESS_ID_MISMATCH",
        ));
    }
    let mut in_job = 0;
    // SAFETY: process and Job handles are live; output is writable.
    if unsafe { IsProcessInJob(process_handle, job.0, &mut in_job) } == 0 || in_job == 0 {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DEBUG_EVENT_PROCESS_NOT_IN_JOB",
        ));
    }
    let creation_time_100ns = query_process_creation_time(process_handle)?;
    let parent_process_id = query_process_parent(process_handle)?;
    let command_line = query_process_command_line(process_handle)?;
    let image_path = query_process_image_path(process_handle)?;
    let (volume_serial, file_id, link_count, length) =
        file_id_from_handle(image_file, Gate::BinaryIdentity)?;
    let sha256 = sha256_open_file(image_file, Gate::BinaryIdentity)?;
    let image_matches_pinned_binary = volume_serial == binary.identity.volume_serial
        && file_id == binary.identity.file_id
        && link_count == 1
        && length == binary.identity.length
        && sha256 == binary.identity.sha256
        && windows_paths_equal(&image_path, &binary.path);
    let image_matches_pinned_conhost = conhost.matches(
        &image_path,
        volume_serial,
        &file_id,
        link_count,
        length,
        &sha256,
    );
    let (image_role, image_signature, command_line_matches_expected) =
        if image_matches_pinned_binary {
            (
                HELPER_CODEX_IMAGE_ROLE,
                "FROZEN_OPENAI_AUTHENTICODE_VERIFIED",
                command_line == format!("\"{}\" --version", binary.path.display()),
            )
        } else if image_matches_pinned_conhost {
            (
                HELPER_CONHOST_IMAGE_ROLE,
                "UNOBSERVED",
                command_line == FIXED_CONHOST_COMMAND_LINE,
            )
        } else {
            (HELPER_UNEXPECTED_IMAGE_ROLE, "UNOBSERVED", false)
        };
    Ok(NativeHelperProcessEvidence {
        process_id,
        parent_process_id,
        creation_time_100ns,
        in_job: true,
        image_path,
        image_volume_serial: volume_serial,
        image_file_id: hex_bytes(&file_id),
        image_link_count: link_count,
        image_length: length,
        image_sha256: sha256,
        image_role,
        image_matches_pinned_binary,
        image_matches_pinned_conhost,
        image_signature,
        windows_build_binding: "UNQUALIFIED",
        command_line,
        command_line_matches_expected,
        command_line_source: HELPER_COMMAND_LINE_SOURCE,
    })
}

fn query_process_creation_time(process_handle: HANDLE) -> Result<u64, Blocker> {
    // SAFETY: zero is the output baseline.
    let mut creation: FILETIME = unsafe { std::mem::zeroed() };
    let mut exit: FILETIME = unsafe { std::mem::zeroed() };
    let mut kernel: FILETIME = unsafe { std::mem::zeroed() };
    let mut user: FILETIME = unsafe { std::mem::zeroed() };
    // SAFETY: process is live and all outputs are exact writable FILETIMEs.
    if unsafe {
        GetProcessTimes(
            process_handle,
            &mut creation,
            &mut exit,
            &mut kernel,
            &mut user,
        )
    } == 0
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_CREATION_TIME_QUERY_FAILED",
        ));
    }
    let value = ((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64;
    if value == 0 {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_CREATION_TIME_INVALID",
        ));
    }
    Ok(value)
}

fn query_process_parent(process_handle: HANDLE) -> Result<u32, Blocker> {
    // SAFETY: zero is the output baseline.
    let mut information: NativeProcessBasicInformation = unsafe { std::mem::zeroed() };
    let mut returned = 0u32;
    // SAFETY: process and exact output structure are live for this call.
    let status = unsafe {
        NtQueryInformationProcess(
            process_handle,
            PROCESS_BASIC_INFORMATION_CLASS,
            (&mut information as *mut NativeProcessBasicInformation).cast(),
            std::mem::size_of::<NativeProcessBasicInformation>() as u32,
            &mut returned,
        )
    };
    if status < 0
        || returned != std::mem::size_of::<NativeProcessBasicInformation>() as u32
        || information.inherited_from_unique_process_id == 0
        || information.inherited_from_unique_process_id > u32::MAX as usize
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_PARENT_QUERY_FAILED",
        ));
    }
    Ok(information.inherited_from_unique_process_id as u32)
}

fn query_process_command_line(process_handle: HANDLE) -> Result<String, Blocker> {
    let mut required = 0u32;
    // SAFETY: documented size query; required is writable.
    let _ = unsafe {
        NtQueryInformationProcess(
            process_handle,
            PROCESS_COMMAND_LINE_INFORMATION_CLASS,
            std::ptr::null_mut(),
            0,
            &mut required,
        )
    };
    if required < std::mem::size_of::<NativeUnicodeString>() as u32
        || required as usize > MAX_COMMAND_LINE_BYTES
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_COMMAND_LINE_SIZE_INVALID",
        ));
    }
    let mut bytes = vec![0u8; required as usize];
    let mut returned = 0u32;
    // SAFETY: process and exact allocated output buffer are live.
    let status = unsafe {
        NtQueryInformationProcess(
            process_handle,
            PROCESS_COMMAND_LINE_INFORMATION_CLASS,
            bytes.as_mut_ptr().cast(),
            bytes.len() as u32,
            &mut returned,
        )
    };
    if status < 0
        || returned > required
        || returned < std::mem::size_of::<NativeUnicodeString>() as u32
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_COMMAND_LINE_QUERY_FAILED",
        ));
    }
    // SAFETY: returned size contains the complete unaligned header.
    let value = unsafe { std::ptr::read_unaligned(bytes.as_ptr().cast::<NativeUnicodeString>()) };
    if value.length == 0
        || value.length % 2 != 0
        || value.length > value.maximum_length
        || value.length as usize > bytes.len()
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_COMMAND_LINE_HEADER_INVALID",
        ));
    }
    let base = bytes.as_ptr() as usize;
    let end = base.checked_add(bytes.len()).ok_or_else(|| {
        Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_COMMAND_LINE_RANGE_OVERFLOW",
        )
    })?;
    let string_start = value.buffer as usize;
    let string_end = string_start
        .checked_add(value.length as usize)
        .ok_or_else(|| {
            Blocker::new(
                Gate::PrelaunchObserved,
                "HELPER_PROCESS_COMMAND_LINE_RANGE_OVERFLOW",
            )
        })?;
    if string_start < base || string_end > end {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_COMMAND_LINE_POINTER_OUTSIDE_BUFFER",
        ));
    }
    // SAFETY: pointer range is inside the owned buffer and length is even.
    let wide = unsafe { std::slice::from_raw_parts(value.buffer, value.length as usize / 2) };
    String::from_utf16(wide).map_err(|_| {
        Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_COMMAND_LINE_NOT_UTF16",
        )
    })
}

fn query_process_image_path(process_handle: HANDLE) -> Result<PathBuf, Blocker> {
    let mut wide = vec![0u16; 32_768];
    let mut length = wide.len() as u32;
    // SAFETY: process is live and the exact UTF-16 output buffer is writable.
    if unsafe { QueryFullProcessImageNameW(process_handle, 0, wide.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length as usize >= wide.len()
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_IMAGE_PATH_QUERY_FAILED",
        ));
    }
    let value = String::from_utf16(&wide[..length as usize]).map_err(|_| {
        Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_PROCESS_IMAGE_PATH_NOT_UTF16",
        )
    })?;
    Ok(PathBuf::from(value))
}

fn windows_paths_equal(left: &Path, right: &Path) -> bool {
    let normalize = |path: &Path| {
        path.to_string_lossy()
            .trim_start_matches(r"\\?\")
            .replace('/', "\\")
            .to_ascii_lowercase()
    };
    normalize(left) == normalize(right)
}

#[derive(Debug)]
struct PinnedConhostImage {
    path: PathBuf,
    file: File,
    volume_serial: u64,
    file_id: [u8; 16],
    link_count: u32,
    length: u64,
    sha256: String,
    _ancestors: HeldDirectoryChain,
}

impl PinnedConhostImage {
    fn open() -> Result<Self, Blocker> {
        let path = PathBuf::from(FIXED_CONHOST_PATH);
        let parent = path.parent().ok_or_else(|| {
            Blocker::new(Gate::PrelaunchObserved, "HELPER_CONHOST_PARENT_MISSING")
        })?;
        let ancestors = HeldDirectoryChain::open(parent, Gate::PrelaunchObserved)?;
        let file = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(&path)
            .map_err(|_| Blocker::new(Gate::PrelaunchObserved, "HELPER_CONHOST_OPEN_FAILED"))?;
        let attributes = file
            .metadata()
            .map_err(|_| Blocker::new(Gate::PrelaunchObserved, "HELPER_CONHOST_METADATA_FAILED"))?
            .file_attributes();
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "HELPER_CONHOST_REPARSE_FORBIDDEN",
            ));
        }
        let (volume_serial, file_id, link_count, length) =
            file_id_from_handle(&file, Gate::PrelaunchObserved)?;
        let (parent_volume, _, _, _) = file_id_from_handle(
            ancestors.last(Gate::PrelaunchObserved)?,
            Gate::PrelaunchObserved,
        )?;
        if volume_serial != ancestors.volume_serial || parent_volume != volume_serial {
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "HELPER_CONHOST_VOLUME_MISMATCH",
            ));
        }
        let sha256 = sha256_open_file(&file, Gate::PrelaunchObserved)?;
        if length != FIXED_CONHOST_LENGTH || sha256 != FIXED_CONHOST_SHA256 {
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "HELPER_CONHOST_FROZEN_CONTENT_MISMATCH",
            ));
        }
        verify_pe_file(&file)?;
        let pinned = Self {
            path,
            file,
            volume_serial,
            file_id,
            link_count,
            length,
            sha256,
            _ancestors: ancestors,
        };
        pinned.revalidate()?;
        Ok(pinned)
    }

    fn revalidate(&self) -> Result<(), Blocker> {
        let (volume_serial, file_id, link_count, length) =
            file_id_from_handle(&self.file, Gate::PrelaunchObserved)?;
        let sha256 = sha256_open_file(&self.file, Gate::PrelaunchObserved)?;
        if volume_serial != self.volume_serial
            || file_id != self.file_id
            || link_count != self.link_count
            || length != self.length
            || sha256 != self.sha256
        {
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "HELPER_PINNED_CONHOST_CHANGED",
            ));
        }
        let reopened = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(&self.path)
            .map_err(|_| {
                Blocker::new(Gate::PrelaunchObserved, "HELPER_CONHOST_PATH_REOPEN_FAILED")
            })?;
        let (path_volume, path_file_id, path_links, path_length) =
            file_id_from_handle(&reopened, Gate::PrelaunchObserved)?;
        if path_volume != volume_serial
            || path_file_id != file_id
            || path_links != link_count
            || path_length != length
        {
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "HELPER_CONHOST_PATH_CHANGED",
            ));
        }
        Ok(())
    }

    fn matches(
        &self,
        image_path: &Path,
        volume_serial: u64,
        file_id: &[u8; 16],
        link_count: u32,
        length: u64,
        sha256: &str,
    ) -> bool {
        windows_paths_equal(image_path, &self.path)
            && volume_serial == self.volume_serial
            && file_id == &self.file_id
            && link_count == self.link_count
            && length == self.length
            && sha256 == self.sha256
    }
}

#[derive(Debug)]
struct HelperChildOutcome {
    exit_code: u32,
    stdout: BoundedCapture,
    stderr: BoundedCapture,
    total_processes: u32,
    active_processes: u32,
    max_observed_total_processes: u32,
    max_observed_active_processes: u32,
    child_process_policy_flags: u32,
    processes: Vec<NativeHelperProcessEvidence>,
    debug_event_codes: Vec<u32>,
    debug_events: Vec<NativeHelperDebugEvent>,
    job_monitor: JobCompletionMonitor,
}

struct HelperChildTransaction {
    job: OwnedKernelHandle,
    process: OwnedKernelHandle,
    thread: OwnedKernelHandle,
    pipes: PipeSet,
    stdout_reader: Option<JoinHandle<Result<BoundedCapture, Blocker>>>,
    stderr_reader: Option<JoinHandle<Result<BoundedCapture, Blocker>>>,
    monitor: Option<JobCompletionMonitor>,
    resumed: bool,
    drained: bool,
    max_observed_total_processes: u32,
    max_observed_active_processes: u32,
    child_process_policy_flags: u32,
    failure_blockers: Vec<&'static str>,
}

impl HelperChildTransaction {
    fn create(
        binary: &PinnedBinary,
        runtime: &RuntimeAuthority,
        environment_entries: &[(String, String)],
        policy: LaunchPolicy,
    ) -> Result<Self, Blocker> {
        let job = create_qualified_job()?;
        // The completion port is associated before any process can be atomically
        // assigned by PROC_THREAD_ATTRIBUTE_JOB_LIST or explicitly assigned.
        let monitor = JobCompletionMonitor::associate(&job)?;
        let pipes = PipeSet::create()?;
        let inherited = pipes.inherited_handles()?;
        let attributes = ProcThreadAttributeList::create(job.0, &inherited, policy)?;
        // SAFETY: zero is the baseline; all required fields are assigned.
        let mut startup: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = inherited[0];
        startup.StartupInfo.hStdOutput = inherited[1];
        startup.StartupInfo.hStdError = inherited[2];
        startup.lpAttributeList = attributes.pointer;

        let mut command_line = wide_null(OsStr::new(&format!(
            "\"{}\" --version",
            binary.path.display()
        )));
        let application = wide_null(binary.path.as_os_str());
        let current_directory = wide_null(runtime.run_root.as_os_str());
        let environment = build_environment_block(environment_entries)?;
        let console_creation_flag = helper_console_creation_flag(policy)?;
        // SAFETY: zero is the output baseline.
        let mut process_information: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
        // SAFETY: all pointers are live for the call. The child starts
        // suspended, under DEBUG_PROCESS, with only the three pipe handles.
        let created = unsafe {
            CreateProcessW(
                application.as_ptr(),
                command_line.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
                CREATE_SUSPENDED
                    | console_creation_flag
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT
                    | DEBUG_PROCESS,
                environment.as_ptr().cast(),
                current_directory.as_ptr(),
                &startup.StartupInfo as *const _,
                &mut process_information,
            )
        };
        // SAFETY: sampled immediately after CreateProcessW.
        let create_error = if created == 0 {
            unsafe { GetLastError() }
        } else {
            0
        };
        if created == 0
            || process_information.hProcess.is_null()
            || process_information.hThread.is_null()
        {
            if !process_information.hProcess.is_null() {
                // SAFETY: partial-success process is directly terminated and closed.
                let _ = unsafe {
                    TerminateProcess(process_information.hProcess, PREFLIGHT_TERMINATE_EXIT)
                };
                let _ = unsafe { WaitForSingleObject(process_information.hProcess, 5_000) };
                let _ = unsafe { CloseHandle(process_information.hProcess) };
            }
            if !process_information.hThread.is_null() {
                // SAFETY: partial-success thread handle is closed once.
                let _ = unsafe { CloseHandle(process_information.hThread) };
            }
            return Err(create_process_blocker(create_error));
        }
        let child_process_policy_flags =
            match query_child_process_policy_flags(process_information.hProcess) {
                Ok(flags) => flags,
                Err(error) => {
                    let _ = unsafe {
                        TerminateProcess(process_information.hProcess, PREFLIGHT_TERMINATE_EXIT)
                    };
                    let _ = unsafe { TerminateJobObject(job.0, PREFLIGHT_TERMINATE_EXIT) };
                    let _ = unsafe { WaitForSingleObject(process_information.hProcess, 5_000) };
                    let _ = unsafe { CloseHandle(process_information.hProcess) };
                    let _ = unsafe { CloseHandle(process_information.hThread) };
                    return Err(error);
                }
            };
        let expected_child_process_policy_flags = u32::from(policy.child_process_restricted);
        if child_process_policy_flags != expected_child_process_policy_flags {
            let _ =
                unsafe { TerminateProcess(process_information.hProcess, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { TerminateJobObject(job.0, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { WaitForSingleObject(process_information.hProcess, 5_000) };
            let _ = unsafe { CloseHandle(process_information.hProcess) };
            let _ = unsafe { CloseHandle(process_information.hThread) };
            return Err(Blocker::new(
                Gate::PrelaunchObserved,
                "CHILD_PROCESS_POLICY_READBACK_MISMATCH",
            ));
        }
        // SAFETY: current thread is now the debugger for this new process tree;
        // preserving kill-on-exit is an additional fail-closed boundary.
        if unsafe { DebugSetProcessKillOnExit(1) } == 0 {
            let _ =
                unsafe { TerminateProcess(process_information.hProcess, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { TerminateJobObject(job.0, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { WaitForSingleObject(process_information.hProcess, 5_000) };
            // SAFETY: both CreateProcess handles are owned here.
            let _ = unsafe { CloseHandle(process_information.hProcess) };
            let _ = unsafe { CloseHandle(process_information.hThread) };
            return Err(Blocker::new(
                Gate::ChildCreatedSuspended,
                "HELPER_DEBUG_KILL_ON_EXIT_ENABLE_FAILED",
            ));
        }
        Ok(Self {
            job,
            process: OwnedKernelHandle(process_information.hProcess),
            thread: OwnedKernelHandle(process_information.hThread),
            pipes,
            stdout_reader: None,
            stderr_reader: None,
            monitor: Some(monitor),
            resumed: false,
            drained: false,
            max_observed_total_processes: 0,
            max_observed_active_processes: 0,
            child_process_policy_flags,
            failure_blockers: Vec::new(),
        })
    }

    fn prepare_job_membership(&mut self, policy: LaunchPolicy) -> Result<(), Blocker> {
        if policy.assigned_after_create {
            // SAFETY: the primary thread is still suspended.
            if unsafe { AssignProcessToJobObject(self.job.0, self.process.0) } == 0 {
                let trigger =
                    Blocker::new(Gate::JobPrepared, "HELPER_ASSIGN_PROCESS_TO_JOB_FAILED");
                return self.cleanup_after_error(trigger);
            }
        }
        let accounting = query_job_accounting(&self.job, Gate::JobPrepared)?;
        self.observe_accounting(&accounting);
        if accounting.TotalProcesses != 1 || accounting.ActiveProcesses != 1 {
            return self.cleanup_after_error(Blocker::new(
                Gate::JobPrepared,
                "HELPER_PRE_RESUME_JOB_MEMBERSHIP_INVALID",
            ));
        }
        Ok(())
    }

    fn observe_accounting(&mut self, accounting: &JOBOBJECT_BASIC_ACCOUNTING_INFORMATION) {
        self.max_observed_total_processes = self
            .max_observed_total_processes
            .max(accounting.TotalProcesses);
        self.max_observed_active_processes = self
            .max_observed_active_processes
            .max(accounting.ActiveProcesses);
    }

    fn sample_accounting(
        &mut self,
        gate: Gate,
    ) -> Result<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, Blocker> {
        let accounting = query_job_accounting(&self.job, gate)?;
        self.observe_accounting(&accounting);
        Ok(accounting)
    }

    fn start_readers(&mut self) -> Result<(), Blocker> {
        self.pipes.stdin_parent.take();
        self.pipes.stdin_child.take();
        self.pipes.stdout_child.take();
        self.pipes.stderr_child.take();
        let stdout =
            self.pipes.stdout_parent.take().ok_or_else(|| {
                Blocker::new(Gate::OutputAndDeadline, "HELPER_STDOUT_PARENT_MISSING")
            })?;
        let stderr =
            self.pipes.stderr_parent.take().ok_or_else(|| {
                Blocker::new(Gate::OutputAndDeadline, "HELPER_STDERR_PARENT_MISSING")
            })?;
        // SAFETY: each unique owned handle is transferred once to File.
        let stdout_file = unsafe { File::from_raw_handle(stdout.into_raw().cast()) };
        // SAFETY: same ownership transfer as stdout.
        let stderr_file = unsafe { File::from_raw_handle(stderr.into_raw().cast()) };
        self.stdout_reader = Some(
            thread::Builder::new()
                .name("pkm-helper-stdout".to_owned())
                .spawn(move || read_bounded(stdout_file, MAX_STDOUT_BYTES))
                .map_err(|_| {
                    Blocker::new(Gate::OutputAndDeadline, "HELPER_STDOUT_READER_START_FAILED")
                })?,
        );
        self.stderr_reader = Some(
            thread::Builder::new()
                .name("pkm-helper-stderr".to_owned())
                .spawn(move || read_bounded(stderr_file, MAX_STDERR_BYTES))
                .map_err(|_| {
                    Blocker::new(Gate::OutputAndDeadline, "HELPER_STDERR_READER_START_FAILED")
                })?,
        );
        Ok(())
    }

    fn run_debugged(
        &mut self,
        binary: &PinnedBinary,
        conhost: &PinnedConhostImage,
    ) -> Result<HelperChildOutcome, Blocker> {
        if let Err(error) = self.start_readers() {
            return self.cleanup_after_error(error);
        }
        // SAFETY: the live primary thread was created suspended exactly once.
        if unsafe { ResumeThread(self.thread.0) } == u32::MAX {
            self.resumed = true;
            return self.cleanup_after_error(Blocker::new(
                Gate::ChildResumed,
                "HELPER_RESUME_FAILED_POSSIBLY_RAN",
            ));
        }
        self.resumed = true;
        let started = Instant::now();
        let mut processes = Vec::new();
        let mut debug_event_codes = Vec::new();
        let mut debug_events = Vec::new();
        let mut debug_exit_processes = Vec::new();
        loop {
            if started.elapsed() >= HELPER_DEADLINE {
                return self.cleanup_after_error(Blocker::new(
                    Gate::OutputAndDeadline,
                    "HELPER_DEBUG_DEADLINE_EXCEEDED",
                ));
            }
            // SAFETY: zero is the output baseline for this bounded wait.
            let mut event: DEBUG_EVENT = unsafe { std::mem::zeroed() };
            // SAFETY: this is the exact debugger thread; event is writable.
            let received = unsafe { WaitForDebugEvent(&mut event, 25) };
            if received != 0 {
                let event_code = event.dwDebugEventCode;
                debug_event_codes.push(event_code);
                if debug_event_codes.len() > 256 {
                    close_unobserved_debug_event_handles(&event);
                    let _ = continue_debug_event(&event);
                    return self.cleanup_after_error(Blocker::new(
                        Gate::PrelaunchObserved,
                        "HELPER_DEBUG_EVENT_LIMIT_EXCEEDED",
                    ));
                }
                if matches!(
                    event_code,
                    CREATE_PROCESS_DEBUG_EVENT | EXIT_PROCESS_DEBUG_EVENT
                ) {
                    let sequence = debug_events.len().try_into().map_err(|_| {
                        Blocker::new(
                            Gate::PrelaunchObserved,
                            "HELPER_DEBUG_LIFECYCLE_SEQUENCE_OVERFLOW",
                        )
                    })?;
                    debug_events.push(NativeHelperDebugEvent {
                        sequence,
                        kind: if event_code == CREATE_PROCESS_DEBUG_EVENT {
                            "CREATE_PROCESS"
                        } else {
                            "EXIT_PROCESS"
                        },
                        process_id: event.dwProcessId,
                        thread_id: event.dwThreadId,
                    });
                }
                let capture = self.handle_debug_event(
                    &event,
                    binary,
                    conhost,
                    &mut processes,
                    &mut debug_exit_processes,
                );
                let continued = continue_debug_event(&event);
                if let Err(error) = capture {
                    let _ = continued;
                    return self.cleanup_after_error(error);
                }
                if let Err(error) = continued {
                    return self.cleanup_after_error(error);
                }
            } else {
                // SAFETY: sampled immediately after WaitForDebugEvent.
                let error = unsafe { GetLastError() };
                if error != ERROR_SEM_TIMEOUT && error != WAIT_TIMEOUT {
                    return self.cleanup_after_error(Blocker::new(
                        Gate::PrelaunchObserved,
                        "HELPER_DEBUG_EVENT_WAIT_FAILED",
                    ));
                }
            }
            if let Some(monitor) = self.monitor.as_mut() {
                if let Err(error) = monitor.drain_nonblocking() {
                    return self.cleanup_after_error(error);
                }
            }
            let accounting = match self.sample_accounting(Gate::ChildResumed) {
                Ok(value) => value,
                Err(error) => return self.cleanup_after_error(error),
            };
            let root_exited =
                debug_exit_processes.contains(&unsafe { GetProcessId(self.process.0) });
            let every_observed_process_exited = processes
                .iter()
                .all(|process| debug_exit_processes.contains(&process.process_id));
            let active_zero_event = self.monitor.as_ref().is_some_and(|monitor| {
                monitor
                    .events
                    .iter()
                    .any(|event| event.kind == "ACTIVE_PROCESS_ZERO")
            });
            if accounting.ActiveProcesses == 0
                && root_exited
                && every_observed_process_exited
                && active_zero_event
            {
                self.drained = true;
                break;
            }
        }

        let final_accounting = self.sample_accounting(Gate::JobDrained)?;
        if final_accounting.ActiveProcesses != 0 {
            return self.cleanup_after_error(Blocker::new(
                Gate::JobDrained,
                "HELPER_FINAL_ACTIVE_PROCESS_NONZERO",
            ));
        }
        if let Some(monitor) = self.monitor.as_mut() {
            monitor.drain_nonblocking()?;
        }
        let mut exit_code = 0u32;
        // SAFETY: root process is signaled and output is writable.
        if unsafe { GetExitCodeProcess(self.process.0, &mut exit_code) } == 0 {
            return Err(Blocker::new(
                Gate::OutputAndDeadline,
                "HELPER_EXIT_CODE_QUERY_FAILED",
            ));
        }
        let stdout = join_capture(&mut self.stdout_reader, "HELPER_STDOUT_READER_JOIN_FAILED")?;
        let stderr = join_capture(&mut self.stderr_reader, "HELPER_STDERR_READER_JOIN_FAILED")?;
        let monitor = self
            .monitor
            .take()
            .ok_or_else(|| Blocker::new(Gate::JobDrained, "HELPER_JOB_MONITOR_MISSING"))?;
        Ok(HelperChildOutcome {
            exit_code,
            stdout,
            stderr,
            total_processes: final_accounting.TotalProcesses,
            active_processes: final_accounting.ActiveProcesses,
            max_observed_total_processes: self.max_observed_total_processes,
            max_observed_active_processes: self.max_observed_active_processes,
            child_process_policy_flags: self.child_process_policy_flags,
            processes,
            debug_event_codes,
            debug_events,
            job_monitor: monitor,
        })
    }

    fn handle_debug_event(
        &self,
        event: &DEBUG_EVENT,
        binary: &PinnedBinary,
        conhost: &PinnedConhostImage,
        processes: &mut Vec<NativeHelperProcessEvidence>,
        exits: &mut Vec<u32>,
    ) -> Result<(), Blocker> {
        match event.dwDebugEventCode {
            CREATE_PROCESS_DEBUG_EVENT => {
                // SAFETY: union arm is selected by dwDebugEventCode.
                let info = unsafe { event.u.CreateProcessInfo };
                if info.hFile.is_null() || info.hProcess.is_null() || info.hThread.is_null() {
                    close_debug_create_handles(info.hFile, info.hProcess, info.hThread);
                    return Err(Blocker::new(
                        Gate::PrelaunchObserved,
                        "HELPER_DEBUG_CREATE_EVENT_HANDLE_MISSING",
                    ));
                }
                // SAFETY: each debug-event handle is uniquely owned by the debugger.
                let image = unsafe { File::from_raw_handle(info.hFile.cast()) };
                let process = OwnedKernelHandle(info.hProcess);
                let _thread = OwnedKernelHandle(info.hThread);
                let evidence = capture_process_evidence(
                    process.0,
                    &image,
                    event.dwProcessId,
                    &self.job,
                    binary,
                    conhost,
                )?;
                if processes.iter().any(|existing| {
                    existing.process_id == evidence.process_id
                        || existing.creation_time_100ns == evidence.creation_time_100ns
                }) {
                    return Err(Blocker::new(
                        Gate::PrelaunchObserved,
                        "HELPER_DEBUG_PID_OR_CREATION_TIME_REUSED",
                    ));
                }
                processes.push(evidence);
                if processes.len() > 2 {
                    return Err(Blocker::new(
                        Gate::PrelaunchObserved,
                        "HELPER_DEBUG_EXTRA_PROCESS_OBSERVED",
                    ));
                }
            }
            EXIT_PROCESS_DEBUG_EVENT => {
                if exits.contains(&event.dwProcessId) {
                    return Err(Blocker::new(
                        Gate::PrelaunchObserved,
                        "HELPER_DEBUG_DUPLICATE_PROCESS_EXIT",
                    ));
                }
                exits.push(event.dwProcessId);
            }
            CREATE_THREAD_DEBUG_EVENT => {
                // SAFETY: union arm is selected by dwDebugEventCode.
                let info = unsafe { event.u.CreateThread };
                if !info.hThread.is_null() {
                    drop(OwnedKernelHandle(info.hThread));
                }
            }
            LOAD_DLL_DEBUG_EVENT => {
                // SAFETY: union arm is selected by dwDebugEventCode.
                let info = unsafe { event.u.LoadDll };
                if !info.hFile.is_null() {
                    // SAFETY: unique debug-event file handle ownership.
                    drop(unsafe { File::from_raw_handle(info.hFile.cast()) });
                }
            }
            EXCEPTION_DEBUG_EVENT
            | EXIT_THREAD_DEBUG_EVENT
            | UNLOAD_DLL_DEBUG_EVENT
            | OUTPUT_DEBUG_STRING_EVENT
            | RIP_EVENT => {}
            _ => {
                return Err(Blocker::new(
                    Gate::PrelaunchObserved,
                    "HELPER_DEBUG_EVENT_CODE_UNKNOWN",
                ));
            }
        }
        Ok(())
    }

    fn cleanup_after_error<T>(&mut self, trigger: Blocker) -> Result<T, Blocker> {
        record_failure_code(&mut self.failure_blockers, trigger.code);
        match self.terminate_and_drain_debugged() {
            Ok(()) => Err(trigger),
            Err(cleanup) => {
                record_failure_code(&mut self.failure_blockers, cleanup.code);
                Err(cleanup)
            }
        }
    }

    fn terminate_and_drain_debugged(&mut self) -> Result<(), Blocker> {
        // SAFETY: direct root termination covers a pre-assignment failure;
        // Job termination covers every assigned descendant.
        let _ = unsafe { TerminateProcess(self.process.0, PREFLIGHT_TERMINATE_EXIT) };
        if unsafe { TerminateJobObject(self.job.0, PREFLIGHT_TERMINATE_EXIT) } == 0 {
            return Err(Blocker::new(
                Gate::JobTerminated,
                "HELPER_TERMINATE_JOB_FAILED",
            ));
        }
        let deadline = Instant::now() + PREFLIGHT_CLEANUP_WAIT;
        loop {
            // SAFETY: zero is the debug-event output baseline.
            let mut event: DEBUG_EVENT = unsafe { std::mem::zeroed() };
            // SAFETY: bounded debugger wait on the creating thread.
            if unsafe { WaitForDebugEvent(&mut event, 25) } != 0 {
                close_unobserved_debug_event_handles(&event);
                continue_debug_event(&event)?;
            } else {
                // SAFETY: sampled immediately after the debug wait.
                let error = unsafe { GetLastError() };
                if error != ERROR_SEM_TIMEOUT && error != WAIT_TIMEOUT {
                    return Err(Blocker::new(
                        Gate::JobDrained,
                        "HELPER_CLEANUP_DEBUG_EVENT_WAIT_FAILED",
                    ));
                }
            }
            if let Some(monitor) = self.monitor.as_mut() {
                monitor.drain_nonblocking()?;
            }
            let accounting = self.sample_accounting(Gate::JobDrained)?;
            // SAFETY: root handle is live for the bounded zero-time wait.
            let root_wait = unsafe { WaitForSingleObject(self.process.0, 0) };
            if root_wait == WAIT_OBJECT_0 && accounting.ActiveProcesses == 0 {
                self.drained = true;
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(Blocker::new(
                    Gate::JobDrained,
                    "HELPER_CLEANUP_DRAIN_TIMEOUT",
                ));
            }
        }
    }
}

fn record_failure_code(codes: &mut Vec<&'static str>, code: &'static str) {
    if !codes.contains(&code) {
        codes.push(code);
    }
}

impl Drop for HelperChildTransaction {
    fn drop(&mut self) {
        if !self.drained {
            // SAFETY: final fail-closed fallback. Job KILL_ON_CLOSE remains
            // active until the Job handle itself is dropped after this body.
            let _ = unsafe { TerminateProcess(self.process.0, PREFLIGHT_TERMINATE_EXIT) };
            let _ = unsafe { TerminateJobObject(self.job.0, PREFLIGHT_TERMINATE_EXIT) };
            self.stdout_reader.take();
            self.stderr_reader.take();
            return;
        }
        let _ = join_capture(&mut self.stdout_reader, "HELPER_STDOUT_READER_JOIN_FAILED");
        let _ = join_capture(&mut self.stderr_reader, "HELPER_STDERR_READER_JOIN_FAILED");
    }
}

fn close_debug_create_handles(file: HANDLE, process: HANDLE, thread: HANDLE) {
    if !file.is_null() {
        // SAFETY: debug-event handle is owned by the debugger exactly once.
        let _ = unsafe { CloseHandle(file) };
    }
    if !process.is_null() {
        // SAFETY: same unique debug-event ownership.
        let _ = unsafe { CloseHandle(process) };
    }
    if !thread.is_null() {
        // SAFETY: same unique debug-event ownership.
        let _ = unsafe { CloseHandle(thread) };
    }
}

fn close_unobserved_debug_event_handles(event: &DEBUG_EVENT) {
    match event.dwDebugEventCode {
        CREATE_PROCESS_DEBUG_EVENT => {
            // SAFETY: union arm is selected by the event code.
            let info = unsafe { event.u.CreateProcessInfo };
            close_debug_create_handles(info.hFile, info.hProcess, info.hThread);
        }
        CREATE_THREAD_DEBUG_EVENT => {
            // SAFETY: union arm is selected by the event code.
            let info = unsafe { event.u.CreateThread };
            if !info.hThread.is_null() {
                // SAFETY: unique debug-event handle.
                let _ = unsafe { CloseHandle(info.hThread) };
            }
        }
        LOAD_DLL_DEBUG_EVENT => {
            // SAFETY: union arm is selected by the event code.
            let info = unsafe { event.u.LoadDll };
            if !info.hFile.is_null() {
                // SAFETY: unique debug-event handle.
                let _ = unsafe { CloseHandle(info.hFile) };
            }
        }
        _ => {}
    }
}

fn continue_debug_event(event: &DEBUG_EVENT) -> Result<(), Blocker> {
    let status = if event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT {
        // SAFETY: union arm is selected by dwDebugEventCode.
        let code = unsafe { event.u.Exception.ExceptionRecord.ExceptionCode };
        if code == EXCEPTION_BREAKPOINT {
            DBG_CONTINUE
        } else {
            DBG_EXCEPTION_NOT_HANDLED
        }
    } else {
        DBG_CONTINUE
    };
    // SAFETY: process/thread/event tuple comes directly from WaitForDebugEvent.
    if unsafe { ContinueDebugEvent(event.dwProcessId, event.dwThreadId, status) } == 0 {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DEBUG_EVENT_CONTINUE_FAILED",
        ));
    }
    Ok(())
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeHelperProcessEvidence {
    pub process_id: u32,
    pub parent_process_id: u32,
    pub creation_time_100ns: u64,
    pub in_job: bool,
    pub image_path: PathBuf,
    pub image_volume_serial: u64,
    pub image_file_id: String,
    pub image_link_count: u32,
    pub image_length: u64,
    pub image_sha256: String,
    pub image_role: &'static str,
    pub image_matches_pinned_binary: bool,
    pub image_matches_pinned_conhost: bool,
    pub image_signature: &'static str,
    pub windows_build_binding: &'static str,
    pub command_line: String,
    pub command_line_matches_expected: bool,
    pub command_line_source: &'static str,
}

impl NativeHelperProcessEvidence {
    fn to_json(&self) -> String {
        format!(
            "{{\"process_id\":{},\"parent_process_id\":{},\"creation_time_100ns\":{},\"in_job\":{},\"image_path\":\"{}\",\"image_volume_serial\":{},\"image_file_id\":\"{}\",\"image_link_count\":{},\"image_length\":{},\"image_sha256\":\"{}\",\"image_role\":\"{}\",\"image_matches_pinned_binary\":{},\"image_matches_pinned_conhost\":{},\"image_signature\":\"{}\",\"windows_build_binding\":\"{}\",\"command_line\":\"{}\",\"command_line_matches_expected\":{},\"command_line_source\":\"{}\"}}",
            self.process_id,
            self.parent_process_id,
            self.creation_time_100ns,
            json_bool(self.in_job),
            json_escape(&self.image_path.to_string_lossy()),
            self.image_volume_serial,
            json_escape(&self.image_file_id),
            self.image_link_count,
            self.image_length,
            json_escape(&self.image_sha256),
            json_escape(self.image_role),
            json_bool(self.image_matches_pinned_binary),
            json_bool(self.image_matches_pinned_conhost),
            json_escape(self.image_signature),
            json_escape(self.windows_build_binding),
            json_escape(&self.command_line),
            json_bool(self.command_line_matches_expected),
            json_escape(self.command_line_source),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeHelperDebugEvent {
    pub sequence: u32,
    pub kind: &'static str,
    pub process_id: u32,
    pub thread_id: u32,
}

impl NativeHelperDebugEvent {
    fn to_json(&self) -> String {
        format!(
            "{{\"sequence\":{},\"kind\":\"{}\",\"process_id\":{},\"thread_id\":{}}}",
            self.sequence,
            json_escape(self.kind),
            self.process_id,
            self.thread_id,
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeHelperJobEvent {
    pub sequence: u32,
    pub kind: &'static str,
    pub process_id: Option<u32>,
}

impl NativeHelperJobEvent {
    fn to_json(&self) -> String {
        format!(
            "{{\"sequence\":{},\"kind\":\"{}\",\"process_id\":{}}}",
            self.sequence,
            json_escape(self.kind),
            json_u32_or_null(self.process_id),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeHelperHomeChangeEvent {
    pub sequence: u32,
    pub action: &'static str,
    pub relative_path: String,
}

impl NativeHelperHomeChangeEvent {
    fn to_json(&self) -> String {
        format!(
            "{{\"sequence\":{},\"action\":\"{}\",\"relative_path\":\"{}\"}}",
            self.sequence,
            json_escape(self.action),
            json_escape(&self.relative_path),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeHelperHomeEntry {
    pub relative_path: String,
    pub kind: &'static str,
    pub reparse_point: bool,
    pub volume_serial: u64,
    pub file_id: String,
    pub parent_file_id: String,
    pub link_count: u32,
    pub length: u64,
    pub sha256: Option<String>,
}

impl NativeHelperHomeEntry {
    fn to_json(&self) -> String {
        format!(
            "{{\"relative_path\":\"{}\",\"kind\":\"{}\",\"reparse_point\":{},\"volume_serial\":{},\"file_id\":\"{}\",\"parent_file_id\":\"{}\",\"link_count\":{},\"length\":{},\"sha256\":{}}}",
            json_escape(&self.relative_path),
            json_escape(self.kind),
            json_bool(self.reparse_point),
            self.volume_serial,
            json_escape(&self.file_id),
            json_escape(&self.parent_file_id),
            self.link_count,
            self.length,
            json_string_or_null(self.sha256.as_deref()),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeHelperDiagnosticVariantResult {
    pub id: &'static str,
    pub console_creation_mode: &'static str,
    pub terminal: &'static str,
    pub blocker: &'static str,
    pub blockers: Vec<&'static str>,
    pub production: bool,
    pub live: bool,
    pub network_usage: &'static str,
    pub credential_access: &'static str,
    pub external_file_access: &'static str,
    pub atomic_job_list: bool,
    pub child_process_restricted: bool,
    pub child_process_policy_readback: bool,
    pub child_process_policy_flags: u32,
    pub assigned_after_create: bool,
    pub run_root: Option<PathBuf>,
    pub debug_process: bool,
    pub job_before_resume: bool,
    pub child_created: bool,
    pub child_resumed: bool,
    pub descendants_zero: bool,
    pub exit_code: Option<u32>,
    pub stdout_sha256: Option<String>,
    pub stderr_sha256: Option<String>,
    pub stdout_total_bytes: usize,
    pub stderr_total_bytes: usize,
    pub stdout_overflow: bool,
    pub stderr_overflow: bool,
    pub job_limit_readback: bool,
    pub job_active_process_limit: u32,
    pub job_limit_flags: u32,
    pub final_total_processes: Option<u32>,
    pub final_active_processes: Option<u32>,
    pub max_observed_total_processes: Option<u32>,
    pub max_observed_active_processes: Option<u32>,
    pub processes: Vec<NativeHelperProcessEvidence>,
    pub debug_events_complete: bool,
    pub debug_events: Vec<NativeHelperDebugEvent>,
    pub job_events_complete: bool,
    pub job_events: Vec<NativeHelperJobEvent>,
    pub codex_home_change_trace_complete: bool,
    pub codex_home_change_events: Vec<NativeHelperHomeChangeEvent>,
    pub codex_home_inventory_complete: bool,
    pub codex_home_inventory: Vec<NativeHelperHomeEntry>,
    pub process_trace_path: Option<PathBuf>,
    pub process_trace_sha256: Option<String>,
    pub job_trace_path: Option<PathBuf>,
    pub job_trace_sha256: Option<String>,
    pub home_change_trace_path: Option<PathBuf>,
    pub home_change_trace_sha256: Option<String>,
    pub home_inventory_path: Option<PathBuf>,
    pub home_inventory_sha256: Option<String>,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
}

impl NativeHelperDiagnosticVariantResult {
    fn record_blocker(&mut self, blocker: &'static str) {
        if !self.blockers.contains(&blocker) {
            self.blockers.push(blocker);
        }
        if self.blocker == HELPER_NEVER_QUALIFIES
            || self.blocker == "HELPER_DIAGNOSTIC_EVIDENCE_INCOMPLETE"
        {
            self.blocker = blocker;
        }
    }

    fn to_json(&self) -> String {
        let blockers = self
            .blockers
            .iter()
            .map(|blocker| format!("\"{}\"", json_escape(blocker)))
            .collect::<Vec<_>>()
            .join(",");
        let processes = self
            .processes
            .iter()
            .map(NativeHelperProcessEvidence::to_json)
            .collect::<Vec<_>>()
            .join(",");
        let job_events = self
            .job_events
            .iter()
            .map(NativeHelperJobEvent::to_json)
            .collect::<Vec<_>>()
            .join(",");
        let debug_events = self
            .debug_events
            .iter()
            .map(NativeHelperDebugEvent::to_json)
            .collect::<Vec<_>>()
            .join(",");
        let home_events = self
            .codex_home_change_events
            .iter()
            .map(NativeHelperHomeChangeEvent::to_json)
            .collect::<Vec<_>>()
            .join(",");
        let inventory = self
            .codex_home_inventory
            .iter()
            .map(NativeHelperHomeEntry::to_json)
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{{\"id\":\"{}\",\"console_creation_mode\":\"{}\",\"terminal\":\"{}\",\"blocker\":\"{}\",\"blockers\":[{}],\"production\":{},\"live\":{},\"network_usage\":\"{}\",\"credential_access\":\"{}\",\"external_file_access\":\"{}\",\"atomic_job_list\":{},\"child_process_restricted\":{},\"child_process_policy_readback\":{},\"child_process_policy_flags\":{},\"assigned_after_create\":{},\"run_root\":{},\"debug_process\":{},\"job_before_resume\":{},\"child_created\":{},\"child_resumed\":{},\"descendants_zero\":{},\"exit_code\":{},\"stdout_sha256\":{},\"stderr_sha256\":{},\"stdout_total_bytes\":{},\"stderr_total_bytes\":{},\"stdout_overflow\":{},\"stderr_overflow\":{},\"job_limit_readback\":{},\"job_active_process_limit\":{},\"job_limit_flags\":{},\"final_total_processes\":{},\"final_active_processes\":{},\"max_observed_total_processes\":{},\"max_observed_active_processes\":{},\"processes\":[{}],\"debug_events_complete\":{},\"debug_events\":[{}],\"job_events_complete\":{},\"job_events\":[{}],\"codex_home_change_trace_complete\":{},\"codex_home_change_events\":[{}],\"codex_home_inventory_complete\":{},\"codex_home_inventory\":[{}],\"process_trace_path\":{},\"process_trace_sha256\":{},\"job_trace_path\":{},\"job_trace_sha256\":{},\"home_change_trace_path\":{},\"home_change_trace_sha256\":{},\"home_inventory_path\":{},\"home_inventory_sha256\":{},\"receipt_path\":{},\"receipt_sha256\":{}}}",
            self.id,
            json_escape(self.console_creation_mode),
            json_escape(self.terminal),
            json_escape(self.blocker),
            blockers,
            json_bool(self.production),
            json_bool(self.live),
            json_escape(self.network_usage),
            json_escape(self.credential_access),
            json_escape(self.external_file_access),
            json_bool(self.atomic_job_list),
            json_bool(self.child_process_restricted),
            json_bool(self.child_process_policy_readback),
            self.child_process_policy_flags,
            json_bool(self.assigned_after_create),
            json_path_or_null(self.run_root.as_deref()),
            json_bool(self.debug_process),
            json_bool(self.job_before_resume),
            json_bool(self.child_created),
            json_bool(self.child_resumed),
            json_bool(self.descendants_zero),
            json_u32_or_null(self.exit_code),
            json_string_or_null(self.stdout_sha256.as_deref()),
            json_string_or_null(self.stderr_sha256.as_deref()),
            self.stdout_total_bytes,
            self.stderr_total_bytes,
            json_bool(self.stdout_overflow),
            json_bool(self.stderr_overflow),
            json_bool(self.job_limit_readback),
            self.job_active_process_limit,
            self.job_limit_flags,
            json_u32_or_null(self.final_total_processes),
            json_u32_or_null(self.final_active_processes),
            json_u32_or_null(self.max_observed_total_processes),
            json_u32_or_null(self.max_observed_active_processes),
            processes,
            json_bool(self.debug_events_complete),
            debug_events,
            json_bool(self.job_events_complete),
            job_events,
            json_bool(self.codex_home_change_trace_complete),
            home_events,
            json_bool(self.codex_home_inventory_complete),
            inventory,
            json_path_or_null(self.process_trace_path.as_deref()),
            json_string_or_null(self.process_trace_sha256.as_deref()),
            json_path_or_null(self.job_trace_path.as_deref()),
            json_string_or_null(self.job_trace_sha256.as_deref()),
            json_path_or_null(self.home_change_trace_path.as_deref()),
            json_string_or_null(self.home_change_trace_sha256.as_deref()),
            json_path_or_null(self.home_inventory_path.as_deref()),
            json_string_or_null(self.home_inventory_sha256.as_deref()),
            json_path_or_null(self.receipt_path.as_deref()),
            json_string_or_null(self.receipt_sha256.as_deref()),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeHelperDiagnosticResult {
    pub exit_code: u8,
    pub terminal: &'static str,
    pub production: bool,
    pub live: bool,
    pub network_usage: &'static str,
    pub credential_access: &'static str,
    pub external_file_access: &'static str,
    pub blockers: Vec<&'static str>,
    pub variants: Vec<NativeHelperDiagnosticVariantResult>,
    pub receipt_path: Option<PathBuf>,
    pub receipt_sha256: Option<String>,
}

impl NativeHelperDiagnosticResult {
    pub fn to_json(&self) -> String {
        let blockers = self
            .blockers
            .iter()
            .map(|blocker| format!("\"{}\"", json_escape(blocker)))
            .collect::<Vec<_>>()
            .join(",");
        let variants = self
            .variants
            .iter()
            .map(NativeHelperDiagnosticVariantResult::to_json)
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{{\"terminal\":\"{}\",\"production\":{},\"live\":{},\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"network_usage\":\"{}\",\"credential_access\":\"{}\",\"external_file_access\":\"{}\",\"blockers\":[{}],\"receipt_path\":{},\"receipt_sha256\":{},\"variants\":[{}]}}",
            json_escape(self.terminal),
            json_bool(self.production),
            json_bool(self.live),
            json_escape(self.network_usage),
            json_escape(self.credential_access),
            json_escape(self.external_file_access),
            blockers,
            json_path_or_null(self.receipt_path.as_deref()),
            json_string_or_null(self.receipt_sha256.as_deref()),
            variants,
        )
    }
}

fn empty_variant(
    policy: LaunchPolicy,
    blocker: &'static str,
) -> NativeHelperDiagnosticVariantResult {
    let blockers = if blocker == HELPER_NEVER_QUALIFIES {
        vec![HELPER_NEVER_QUALIFIES]
    } else {
        vec![HELPER_NEVER_QUALIFIES, blocker]
    };
    NativeHelperDiagnosticVariantResult {
        id: policy.id,
        console_creation_mode: helper_console_creation_mode(policy).unwrap_or("INVALID"),
        terminal: HELPER_TERMINAL,
        blocker,
        blockers,
        production: false,
        live: false,
        network_usage: "UNOBSERVED",
        credential_access: "UNOBSERVED",
        external_file_access: "UNOBSERVED",
        atomic_job_list: policy.atomic_job_list,
        child_process_restricted: policy.child_process_restricted,
        child_process_policy_readback: false,
        child_process_policy_flags: 0,
        assigned_after_create: policy.assigned_after_create,
        run_root: None,
        debug_process: true,
        job_before_resume: true,
        child_created: false,
        child_resumed: false,
        descendants_zero: false,
        exit_code: None,
        stdout_sha256: None,
        stderr_sha256: None,
        stdout_total_bytes: 0,
        stderr_total_bytes: 0,
        stdout_overflow: false,
        stderr_overflow: false,
        job_limit_readback: false,
        job_active_process_limit: 0,
        job_limit_flags: 0,
        final_total_processes: None,
        final_active_processes: None,
        max_observed_total_processes: None,
        max_observed_active_processes: None,
        processes: Vec::new(),
        debug_events_complete: false,
        debug_events: Vec::new(),
        job_events_complete: false,
        job_events: Vec::new(),
        codex_home_change_trace_complete: false,
        codex_home_change_events: Vec::new(),
        codex_home_inventory_complete: false,
        codex_home_inventory: Vec::new(),
        process_trace_path: None,
        process_trace_sha256: None,
        job_trace_path: None,
        job_trace_sha256: None,
        home_change_trace_path: None,
        home_change_trace_sha256: None,
        home_inventory_path: None,
        home_inventory_sha256: None,
        receipt_path: None,
        receipt_sha256: None,
    }
}

fn base_result() -> NativeHelperDiagnosticResult {
    NativeHelperDiagnosticResult {
        exit_code: 3,
        terminal: HELPER_TERMINAL,
        production: false,
        live: false,
        network_usage: "UNOBSERVED",
        credential_access: "UNOBSERVED",
        external_file_access: "UNOBSERVED",
        blockers: vec![HELPER_NEVER_QUALIFIES, HELPER_ETW_BLOCKER],
        variants: Vec::new(),
        receipt_path: None,
        receipt_sha256: None,
    }
}

fn validate_variant_contract(
    variant: &NativeHelperDiagnosticVariantResult,
    qualifier_process_id: u32,
) -> Result<(), Blocker> {
    let (expected_console_mode, expected_child_restriction) = match variant.id {
        "B" | "D" => ("CREATE_NO_WINDOW", false),
        "E" => ("DETACHED_PROCESS", false),
        "F" => ("DETACHED_PROCESS", true),
        _ => {
            return Err(Blocker::new(
                Gate::Manifest,
                "HELPER_CONSOLE_CREATION_MODE_INVALID",
            ));
        }
    };
    if variant.terminal != HELPER_TERMINAL
        || variant.console_creation_mode != expected_console_mode
        || variant.child_process_restricted != expected_child_restriction
        || !variant.child_process_policy_readback
        || variant.child_process_policy_flags != u32::from(expected_child_restriction)
        || variant.production
        || variant.live
        || variant.network_usage != "UNOBSERVED"
        || variant.credential_access != "UNOBSERVED"
        || variant.external_file_access != "UNOBSERVED"
        || !variant.debug_process
        || !variant.job_before_resume
        || !variant.blockers.contains(&HELPER_NEVER_QUALIFIES)
    {
        return Err(Blocker::new(
            Gate::Manifest,
            "HELPER_DIAGNOSTIC_FAIL_CLOSED_CONTRACT_INVALID",
        ));
    }
    if !variant.child_created
        || !variant.child_resumed
        || !variant.descendants_zero
        || variant.exit_code != Some(0)
        || variant.stdout_overflow
        || variant.stderr_overflow
        || variant.stdout_total_bytes != EXPECTED_CODEX_VERSION.len() + 1
        || variant.stderr_total_bytes != 0
        || variant.stdout_sha256.as_deref()
            != Some("sha256:47e8650c39eae3ea896e5873f03a97a65d183d81cd0d86616e8b83d7d87877ca")
        || variant.stderr_sha256.as_deref()
            != Some("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "HELPER_DIAGNOSTIC_OUTPUT_OR_DRAIN_INCOMPLETE",
        ));
    }
    if matches!(variant.id, "E" | "F") {
        return validate_detached_variant_contract(variant, qualifier_process_id);
    }
    if !variant.job_limit_readback
        || variant.job_active_process_limit != 1
        || variant.job_limit_flags
            != (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
        || variant.final_total_processes != Some(2)
        || variant.final_active_processes != Some(0)
        || variant.max_observed_total_processes != Some(2)
        || variant.max_observed_active_processes != Some(2)
        || variant.processes.len() != 2
        || !variant.debug_events_complete
        || !variant.job_events_complete
        || !variant.blockers.contains(&HELPER_CONHOST_BLOCKER)
        || !variant
            .blockers
            .contains(&HELPER_JOB_LIMIT_OBSERVATION_BLOCKER)
    {
        return Err(Blocker::new(
            Gate::JobDrained,
            "HELPER_DIAGNOSTIC_PROCESS_OR_JOB_EVIDENCE_INCOMPLETE",
        ));
    }

    let root = &variant.processes[0];
    let conhost = &variant.processes[1];
    if root.process_id == 0
        || conhost.process_id == 0
        || root.process_id == conhost.process_id
        || root.creation_time_100ns == 0
        || conhost.creation_time_100ns == 0
        || root.creation_time_100ns == conhost.creation_time_100ns
        || root.parent_process_id != qualifier_process_id
        || conhost.parent_process_id != root.process_id
        || !root.in_job
        || !conhost.in_job
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DIAGNOSTIC_PID_PARENT_OR_JOB_MISMATCH",
        ));
    }
    if root.image_file_id.is_empty()
        || root.image_volume_serial == 0
        || root.image_length != native_preflight_contract().binary_length
        || root.image_link_count != 1
        || root.image_sha256 != EXPECTED_CODEX_EXE_SHA256
        || root.image_role != HELPER_CODEX_IMAGE_ROLE
        || !root.image_matches_pinned_binary
        || root.image_matches_pinned_conhost
        || root.image_signature != "FROZEN_OPENAI_AUTHENTICODE_VERIFIED"
        || root.windows_build_binding != "UNQUALIFIED"
        || !root.command_line_matches_expected
        || root.command_line_source != HELPER_COMMAND_LINE_SOURCE
        || conhost.image_file_id.is_empty()
        || conhost.image_volume_serial == 0
        || conhost.image_length != FIXED_CONHOST_LENGTH
        || conhost.image_link_count != 2
        || conhost.image_sha256 != FIXED_CONHOST_SHA256
        || conhost.image_role != HELPER_CONHOST_IMAGE_ROLE
        || conhost.image_matches_pinned_binary
        || !conhost.image_matches_pinned_conhost
        || conhost.image_signature != "UNOBSERVED"
        || conhost.windows_build_binding != "UNQUALIFIED"
        || !conhost.command_line_matches_expected
        || conhost.command_line != FIXED_CONHOST_COMMAND_LINE
        || !windows_paths_equal(&conhost.image_path, Path::new(FIXED_CONHOST_PATH))
        || conhost.command_line_source != HELPER_COMMAND_LINE_SOURCE
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DIAGNOSTIC_IMAGE_OR_COMMAND_LINE_MISMATCH",
        ));
    }
    let expected_debug_lifecycle = [
        ("CREATE_PROCESS", root.process_id),
        ("CREATE_PROCESS", conhost.process_id),
        ("EXIT_PROCESS", root.process_id),
        ("EXIT_PROCESS", conhost.process_id),
    ];
    if variant.debug_events.len() != expected_debug_lifecycle.len()
        || variant
            .debug_events
            .iter()
            .zip(expected_debug_lifecycle)
            .enumerate()
            .any(|(index, (event, expected))| {
                event.sequence != index as u32
                    || event.kind != expected.0
                    || event.process_id != expected.1
                    || event.thread_id == 0
            })
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DIAGNOSTIC_DEBUG_EVENT_CLOSURE_MISMATCH",
        ));
    }

    let new_pids = variant
        .job_events
        .iter()
        .filter(|event| event.kind == "NEW_PROCESS")
        .filter_map(|event| event.process_id)
        .collect::<Vec<_>>();
    let exit_pids = variant
        .job_events
        .iter()
        .filter(|event| event.kind == "EXIT_PROCESS")
        .filter_map(|event| event.process_id)
        .collect::<Vec<_>>();
    let process_pids = variant
        .processes
        .iter()
        .map(|process| process.process_id)
        .collect::<Vec<_>>();
    if new_pids.len() != 2
        || exit_pids.len() != 2
        || !process_pids
            .iter()
            .all(|pid| new_pids.contains(pid) && exit_pids.contains(pid))
        || variant
            .job_events
            .last()
            .is_none_or(|event| event.kind != "ACTIVE_PROCESS_ZERO")
        || variant
            .job_events
            .iter()
            .any(|event| event.kind == "ACTIVE_PROCESS_LIMIT")
        || variant.job_events.len() != 5
        || variant
            .job_events
            .iter()
            .enumerate()
            .any(|(index, event)| event.sequence != index as u32)
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DIAGNOSTIC_JOB_EVENT_CLOSURE_MISMATCH",
        ));
    }
    let expected_job_lifecycle = [
        ("NEW_PROCESS", Some(root.process_id)),
        ("NEW_PROCESS", Some(conhost.process_id)),
        ("EXIT_PROCESS", Some(root.process_id)),
        ("EXIT_PROCESS", Some(conhost.process_id)),
        ("ACTIVE_PROCESS_ZERO", None),
    ];
    if variant
        .job_events
        .iter()
        .zip(expected_job_lifecycle)
        .any(|(event, expected)| event.kind != expected.0 || event.process_id != expected.1)
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DIAGNOSTIC_JOB_EVENT_ORDER_MISMATCH",
        ));
    }
    if !variant.codex_home_change_trace_complete
        || !variant.codex_home_inventory_complete
        || variant.codex_home_inventory.is_empty()
        || variant
            .codex_home_inventory
            .iter()
            .any(|entry| entry.reparse_point || (entry.kind == "file" && entry.link_count != 1))
    {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "HELPER_DIAGNOSTIC_HOME_EVIDENCE_INCOMPLETE",
        ));
    }
    validate_home_change_trace(&variant.codex_home_change_events)?;
    validate_home_inventory_closure(
        &variant.codex_home_change_events,
        &variant.codex_home_inventory,
    )?;
    Ok(())
}

fn validate_detached_variant_contract(
    variant: &NativeHelperDiagnosticVariantResult,
    qualifier_process_id: u32,
) -> Result<(), Blocker> {
    if !variant.atomic_job_list
        || variant.assigned_after_create
        || !variant.job_limit_readback
        || variant.job_active_process_limit != 1
        || variant.job_limit_flags
            != (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
        || variant.final_total_processes != Some(1)
        || variant.final_active_processes != Some(0)
        || variant.max_observed_total_processes != Some(1)
        || variant.max_observed_active_processes != Some(1)
        || variant.processes.len() != 1
        || !variant.debug_events_complete
        || !variant.job_events_complete
        || variant.blockers.contains(&HELPER_CONHOST_BLOCKER)
        || variant
            .blockers
            .contains(&HELPER_JOB_LIMIT_OBSERVATION_BLOCKER)
    {
        return Err(Blocker::new(
            Gate::JobDrained,
            "HELPER_DETACHED_PROCESS_OR_JOB_EVIDENCE_INCOMPLETE",
        ));
    }

    let root = &variant.processes[0];
    if root.process_id == 0
        || root.creation_time_100ns == 0
        || root.parent_process_id != qualifier_process_id
        || !root.in_job
        || root.image_file_id.is_empty()
        || root.image_volume_serial == 0
        || root.image_length != native_preflight_contract().binary_length
        || root.image_link_count != 1
        || root.image_sha256 != EXPECTED_CODEX_EXE_SHA256
        || root.image_role != HELPER_CODEX_IMAGE_ROLE
        || !root.image_matches_pinned_binary
        || root.image_matches_pinned_conhost
        || root.image_signature != "FROZEN_OPENAI_AUTHENTICODE_VERIFIED"
        || root.windows_build_binding != "UNQUALIFIED"
        || !root.command_line_matches_expected
        || root.command_line_source != HELPER_COMMAND_LINE_SOURCE
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DETACHED_IMAGE_OR_COMMAND_LINE_MISMATCH",
        ));
    }

    let expected_debug_lifecycle = [
        ("CREATE_PROCESS", root.process_id),
        ("EXIT_PROCESS", root.process_id),
    ];
    if variant.debug_events.len() != expected_debug_lifecycle.len()
        || variant
            .debug_events
            .iter()
            .zip(expected_debug_lifecycle)
            .enumerate()
            .any(|(index, (event, expected))| {
                event.sequence != index as u32
                    || event.kind != expected.0
                    || event.process_id != expected.1
                    || event.thread_id == 0
            })
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DETACHED_DEBUG_EVENT_CLOSURE_MISMATCH",
        ));
    }

    let expected_job_lifecycle = [
        ("NEW_PROCESS", Some(root.process_id)),
        ("EXIT_PROCESS", Some(root.process_id)),
        ("ACTIVE_PROCESS_ZERO", None),
    ];
    if variant.job_events.len() != expected_job_lifecycle.len()
        || variant
            .job_events
            .iter()
            .zip(expected_job_lifecycle)
            .enumerate()
            .any(|(index, (event, expected))| {
                event.sequence != index as u32
                    || event.kind != expected.0
                    || event.process_id != expected.1
            })
    {
        return Err(Blocker::new(
            Gate::PrelaunchObserved,
            "HELPER_DETACHED_JOB_EVENT_CLOSURE_MISMATCH",
        ));
    }

    if !variant.codex_home_change_trace_complete
        || !variant.codex_home_inventory_complete
        || variant.codex_home_inventory.is_empty()
        || variant
            .codex_home_inventory
            .iter()
            .any(|entry| entry.reparse_point || (entry.kind == "file" && entry.link_count != 1))
    {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "HELPER_DETACHED_HOME_EVIDENCE_INCOMPLETE",
        ));
    }
    validate_home_change_trace(&variant.codex_home_change_events)?;
    validate_home_inventory_closure(
        &variant.codex_home_change_events,
        &variant.codex_home_inventory,
    )?;
    Ok(())
}

fn validate_home_change_trace(events: &[NativeHelperHomeChangeEvent]) -> Result<(), Blocker> {
    for (index, event) in events.iter().enumerate() {
        if event.sequence != index as u32
            || event.relative_path.is_empty()
            || event.relative_path.contains(':')
            || Path::new(&event.relative_path).is_absolute()
            || Path::new(&event.relative_path)
                .components()
                .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
        {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_CHANGE_SEQUENCE_INVALID",
            ));
        }
        if event.action == "RENAMED_OLD_NAME"
            && events
                .get(index + 1)
                .is_none_or(|next| next.action != "RENAMED_NEW_NAME")
        {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_RENAME_PAIR_INCOMPLETE",
            ));
        }
        if event.action == "RENAMED_NEW_NAME"
            && (index == 0 || events[index - 1].action != "RENAMED_OLD_NAME")
        {
            return Err(Blocker::new(
                Gate::FileBoundary,
                "HELPER_HOME_RENAME_PAIR_INCOMPLETE",
            ));
        }
    }
    Ok(())
}

fn validate_home_inventory_closure(
    events: &[NativeHelperHomeChangeEvent],
    inventory: &[NativeHelperHomeEntry],
) -> Result<(), Blocker> {
    let mut live_paths = BTreeSet::new();
    let mut pending_rename = None;
    for event in events {
        let normalized = event.relative_path.to_ascii_lowercase();
        match event.action {
            "ADDED" => {
                if !live_paths.insert(normalized) {
                    return Err(Blocker::new(
                        Gate::FileBoundary,
                        "HELPER_HOME_EVENT_DUPLICATE_ADD",
                    ));
                }
            }
            "MODIFIED" => {
                if !live_paths.contains(&normalized) {
                    return Err(Blocker::new(
                        Gate::FileBoundary,
                        "HELPER_HOME_EVENT_MODIFIED_UNKNOWN_PATH",
                    ));
                }
            }
            "REMOVED" => {
                if !live_paths.remove(&normalized) {
                    return Err(Blocker::new(
                        Gate::FileBoundary,
                        "HELPER_HOME_EVENT_REMOVED_UNKNOWN_PATH",
                    ));
                }
            }
            "RENAMED_OLD_NAME" => {
                if !live_paths.remove(&normalized) {
                    return Err(Blocker::new(
                        Gate::FileBoundary,
                        "HELPER_HOME_EVENT_RENAMED_UNKNOWN_PATH",
                    ));
                }
                pending_rename = Some(normalized);
            }
            "RENAMED_NEW_NAME" => {
                if pending_rename.take().is_none() || !live_paths.insert(normalized) {
                    return Err(Blocker::new(
                        Gate::FileBoundary,
                        "HELPER_HOME_EVENT_RENAME_TARGET_INVALID",
                    ));
                }
            }
            _ => {
                return Err(Blocker::new(
                    Gate::FileBoundary,
                    "HELPER_HOME_EVENT_ACTION_INVALID",
                ));
            }
        }
    }
    if pending_rename.is_some() {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "HELPER_HOME_EVENT_RENAME_PAIR_INCOMPLETE",
        ));
    }
    let inventory_paths = inventory
        .iter()
        .map(|entry| entry.relative_path.to_ascii_lowercase())
        .collect::<BTreeSet<_>>();
    if live_paths != inventory_paths {
        return Err(Blocker::new(
            Gate::FileBoundary,
            "HELPER_HOME_EVENT_INVENTORY_CLOSURE_MISMATCH",
        ));
    }
    Ok(())
}

fn validate_result_contract(result: &NativeHelperDiagnosticResult) -> Result<(), Blocker> {
    if result.exit_code != 3
        || result.terminal != HELPER_TERMINAL
        || result.production
        || result.live
        || result.network_usage != "UNOBSERVED"
        || result.credential_access != "UNOBSERVED"
        || result.external_file_access != "UNOBSERVED"
        || !result.blockers.contains(&HELPER_NEVER_QUALIFIES)
        || !result.blockers.contains(&HELPER_ETW_BLOCKER)
    {
        return Err(Blocker::new(
            Gate::Manifest,
            "HELPER_DIAGNOSTIC_SUMMARY_CONTRACT_INVALID",
        ));
    }
    Ok(())
}

#[derive(Debug)]
struct HelperAudit {
    runtime: RuntimeAuthority,
    environment: Vec<(String, String)>,
    binary: Option<NtfsFileIdentity>,
    stdin: Option<CreatedArtifact>,
    prompt: Option<CreatedArtifact>,
    schema: Option<CreatedArtifact>,
    stdout: Option<CreatedArtifact>,
    stderr: Option<CreatedArtifact>,
    process_trace: Option<CreatedArtifact>,
    job_trace: Option<CreatedArtifact>,
    home_change_trace: Option<CreatedArtifact>,
    home_inventory: Option<CreatedArtifact>,
}

impl HelperAudit {
    fn new(runtime: RuntimeAuthority, environment: Vec<(String, String)>) -> Self {
        Self {
            runtime,
            environment,
            binary: None,
            stdin: None,
            prompt: None,
            schema: None,
            stdout: None,
            stderr: None,
            process_trace: None,
            job_trace: None,
            home_change_trace: None,
            home_inventory: None,
        }
    }
}

pub fn run_native_helper_diagnostic() -> NativeHelperDiagnosticResult {
    let maintenance = match MaintenanceMutex::acquire() {
        Ok(value) => value,
        Err(error) => return helper_result_failure(error.code, Vec::new()),
    };
    let binary = match PinnedBinary::open() {
        Ok(value) => value,
        Err(error) => return helper_result_failure(error.code, Vec::new()),
    };
    let conhost = match PinnedConhostImage::open() {
        Ok(value) => value,
        Err(error) => return helper_result_failure(error.code, Vec::new()),
    };
    let mut variants = Vec::with_capacity(HELPER_POLICIES.len());
    for (index, policy) in HELPER_POLICIES.iter().copied().enumerate() {
        variants.push(run_helper_variant(index, policy, &binary, &conhost));
    }
    drop(conhost);
    drop(binary);
    let release_blocker = maintenance.release().err().map(|error| error.code);
    persist_helper_summary(variants, release_blocker)
}

fn run_helper_variant(
    index: usize,
    policy: LaunchPolicy,
    binary: &PinnedBinary,
    conhost: &PinnedConhostImage,
) -> NativeHelperDiagnosticVariantResult {
    let prefix = match HELPER_VARIANT_PREFIXES.get(index) {
        Some(value) => *value,
        None => return empty_variant(policy, "HELPER_VARIANT_INDEX_INVALID"),
    };
    let runtime = match RuntimeAuthority::create_with_prefix(prefix) {
        Ok(value) => value,
        Err(error) => return empty_variant(policy, error.code),
    };
    let environment = match build_environment_entries(&runtime) {
        Ok(value) => value,
        Err(error) => {
            let mut variant = empty_variant(policy, error.code);
            variant.run_root = Some(runtime.run_root.clone());
            return variant;
        }
    };
    let mut audit = HelperAudit::new(runtime, environment);
    let mut variant = empty_variant(policy, HELPER_NEVER_QUALIFIES);
    variant.run_root = Some(audit.runtime.run_root.clone());

    let sealed = prepare_helper_sealed_artifacts(&mut audit);
    if let Err(error) = sealed {
        variant.record_blocker(error.code);
        return persist_helper_variant(&mut audit, variant);
    }
    audit.binary = Some(binary.identity.clone());
    if let Err(error) = binary.revalidate() {
        variant.record_blocker(error.code);
        return persist_helper_variant(&mut audit, variant);
    }
    let watcher = match DirectoryWatcher::start(&audit.runtime.codex_home) {
        Ok(value) => value,
        Err(error) => {
            variant.record_blocker(error.code);
            return persist_helper_variant(&mut audit, variant);
        }
    };
    let attempt = execute_helper_probe(&mut audit, &mut variant, binary, conhost, policy);
    match watcher.stop_and_collect() {
        Ok(events) => {
            variant.codex_home_change_events = events;
            variant.codex_home_change_trace_complete = true;
        }
        Err(error) => {
            variant.codex_home_change_trace_complete = false;
            variant.record_blocker(error.code);
        }
    }
    match inventory_codex_home(&audit.runtime.codex_home) {
        Ok(inventory) => {
            variant.codex_home_inventory = inventory;
            variant.codex_home_inventory_complete = true;
        }
        Err(error) => {
            variant.codex_home_inventory_complete = false;
            variant.record_blocker(error.code);
        }
    }
    if variant
        .processes
        .iter()
        .any(|process| process.image_role == HELPER_CONHOST_IMAGE_ROLE)
    {
        variant.record_blocker(HELPER_CONHOST_BLOCKER);
    }
    if variant.job_active_process_limit == 1 && variant.max_observed_active_processes == Some(2) {
        variant.record_blocker(HELPER_JOB_LIMIT_OBSERVATION_BLOCKER);
    }
    if let Err(error) = attempt {
        variant.record_blocker(error.code);
    } else {
        if let Err(error) = validate_variant_contract(&variant, unsafe { GetCurrentProcessId() }) {
            variant.record_blocker(error.code);
        }
    }
    persist_helper_variant(&mut audit, variant)
}

fn prepare_helper_sealed_artifacts(audit: &mut HelperAudit) -> Result<(), Blocker> {
    audit.stdin = Some(create_new_artifact(
        &audit.runtime.sealed.join("stdin.bin"),
        &audit.runtime.sealed_directory,
        PREFLIGHT_STDIN,
        Gate::Manifest,
    )?);
    audit.prompt = Some(create_new_artifact(
        &audit.runtime.sealed.join("prompt.txt"),
        &audit.runtime.sealed_directory,
        PREFLIGHT_PROMPT,
        Gate::Manifest,
    )?);
    audit.schema = Some(create_new_artifact(
        &audit.runtime.sealed.join("schema.json"),
        &audit.runtime.sealed_directory,
        PREFLIGHT_SCHEMA,
        Gate::Manifest,
    )?);
    Ok(())
}

fn execute_helper_probe(
    audit: &mut HelperAudit,
    variant: &mut NativeHelperDiagnosticVariantResult,
    binary: &PinnedBinary,
    conhost: &PinnedConhostImage,
    policy: LaunchPolicy,
) -> Result<(), Blocker> {
    let mut child =
        HelperChildTransaction::create(binary, &audit.runtime, &audit.environment, policy)?;
    variant.child_created = true;
    if let Err(error) = child.prepare_job_membership(policy) {
        variant.descendants_zero = child.drained;
        variant.max_observed_total_processes = Some(child.max_observed_total_processes);
        variant.max_observed_active_processes = Some(child.max_observed_active_processes);
        for code in child.failure_blockers.iter().copied() {
            variant.record_blocker(code);
        }
        return Err(error);
    }
    if let Err(error) = binary.revalidate() {
        let failure: Result<(), Blocker> = child.cleanup_after_error(error);
        for code in child.failure_blockers.iter().copied() {
            variant.record_blocker(code);
        }
        return failure;
    }
    let outcome = match child.run_debugged(binary, conhost) {
        Ok(value) => value,
        Err(error) => {
            variant.child_resumed = child.resumed;
            variant.descendants_zero = child.drained;
            variant.max_observed_total_processes = Some(child.max_observed_total_processes);
            variant.max_observed_active_processes = Some(child.max_observed_active_processes);
            for code in child.failure_blockers.iter().copied() {
                variant.record_blocker(code);
            }
            return Err(error);
        }
    };
    let output_validation = validate_helper_output(&outcome);
    variant.child_resumed = child.resumed;
    variant.descendants_zero = child.drained;
    variant.exit_code = Some(outcome.exit_code);
    variant.stdout_total_bytes = outcome.stdout.total_bytes;
    variant.stderr_total_bytes = outcome.stderr.total_bytes;
    variant.stdout_overflow = outcome.stdout.overflow;
    variant.stderr_overflow = outcome.stderr.overflow;
    variant.final_total_processes = Some(outcome.total_processes);
    variant.final_active_processes = Some(outcome.active_processes);
    variant.max_observed_total_processes = Some(outcome.max_observed_total_processes);
    variant.max_observed_active_processes = Some(outcome.max_observed_active_processes);
    variant.processes = outcome.processes;
    variant.debug_events = outcome.debug_events;
    variant.debug_events_complete = debug_event_closure_complete(
        &variant.processes,
        &variant.debug_events,
        &outcome.debug_event_codes,
    );
    variant.job_limit_readback = outcome.job_monitor.readback_complete;
    variant.child_process_policy_readback = true;
    variant.child_process_policy_flags = outcome.child_process_policy_flags;
    variant.job_active_process_limit = outcome.job_monitor.active_process_limit;
    variant.job_limit_flags = outcome.job_monitor.limit_flags;
    variant.job_events = outcome.job_monitor.events;
    variant.job_events_complete =
        job_event_closure_complete(&variant.processes, &variant.job_events);
    audit.stdout = Some(create_new_artifact(
        &audit.runtime.output.join("stdout.raw"),
        &audit.runtime.output_directory,
        &outcome.stdout.bytes,
        Gate::OutputAndDeadline,
    )?);
    audit.stderr = Some(create_new_artifact(
        &audit.runtime.output.join("stderr.raw"),
        &audit.runtime.output_directory,
        &outcome.stderr.bytes,
        Gate::OutputAndDeadline,
    )?);
    variant.stdout_sha256 = audit
        .stdout
        .as_ref()
        .map(|artifact| artifact.identity.sha256.clone());
    variant.stderr_sha256 = audit
        .stderr
        .as_ref()
        .map(|artifact| artifact.identity.sha256.clone());
    binary.revalidate()?;
    conhost.revalidate()?;
    output_validation?;
    drop(child);
    Ok(())
}

fn validate_helper_output(outcome: &HelperChildOutcome) -> Result<(), Blocker> {
    if outcome.stdout.overflow || outcome.stderr.overflow {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "HELPER_OUTPUT_LIMIT_EXCEEDED",
        ));
    }
    if outcome.exit_code != 0 {
        return Err(Blocker::new(Gate::OutputAndDeadline, "HELPER_EXIT_NONZERO"));
    }
    let stdout = std::str::from_utf8(&outcome.stdout.bytes)
        .map_err(|_| Blocker::new(Gate::OutputAndDeadline, "HELPER_STDOUT_NOT_UTF8"))?;
    let stderr = std::str::from_utf8(&outcome.stderr.bytes)
        .map_err(|_| Blocker::new(Gate::OutputAndDeadline, "HELPER_STDERR_NOT_UTF8"))?;
    if stdout != format!("{EXPECTED_CODEX_VERSION}\n") || !stderr.is_empty() {
        return Err(Blocker::new(
            Gate::OutputAndDeadline,
            "HELPER_VERSION_OUTPUT_MISMATCH",
        ));
    }
    Ok(())
}

fn debug_event_closure_complete(
    processes: &[NativeHelperProcessEvidence],
    lifecycle: &[NativeHelperDebugEvent],
    event_codes: &[u32],
) -> bool {
    let process_ids = processes
        .iter()
        .map(|process| process.process_id)
        .collect::<BTreeSet<_>>();
    let create_ids = lifecycle
        .iter()
        .filter(|event| event.kind == "CREATE_PROCESS")
        .map(|event| event.process_id)
        .collect::<BTreeSet<_>>();
    let exit_ids = lifecycle
        .iter()
        .filter(|event| event.kind == "EXIT_PROCESS")
        .map(|event| event.process_id)
        .collect::<BTreeSet<_>>();
    !processes.is_empty()
        && lifecycle.len() == processes.len() * 2
        && lifecycle
            .iter()
            .enumerate()
            .all(|(index, event)| event.sequence == index as u32)
        && processes.iter().enumerate().all(|(index, process)| {
            lifecycle[index].kind == "CREATE_PROCESS"
                && lifecycle[index].process_id == process.process_id
                && lifecycle[index + processes.len()].kind == "EXIT_PROCESS"
                && lifecycle[index + processes.len()].process_id == process.process_id
        })
        && process_ids == create_ids
        && process_ids == exit_ids
        && event_codes
            .iter()
            .filter(|code| **code == CREATE_PROCESS_DEBUG_EVENT)
            .count()
            == processes.len()
        && event_codes
            .iter()
            .filter(|code| **code == EXIT_PROCESS_DEBUG_EVENT)
            .count()
            == processes.len()
}

fn job_event_closure_complete(
    processes: &[NativeHelperProcessEvidence],
    events: &[NativeHelperJobEvent],
) -> bool {
    if processes.is_empty()
        || events.len() != processes.len() * 2 + 1
        || events
            .iter()
            .enumerate()
            .any(|(index, event)| event.sequence != index as u32)
        || events
            .iter()
            .any(|event| event.kind == "ACTIVE_PROCESS_LIMIT")
        || events
            .last()
            .is_none_or(|event| event.kind != "ACTIVE_PROCESS_ZERO")
    {
        return false;
    }
    let process_ids = processes
        .iter()
        .map(|process| process.process_id)
        .collect::<BTreeSet<_>>();
    let new_ids = events
        .iter()
        .filter(|event| event.kind == "NEW_PROCESS")
        .filter_map(|event| event.process_id)
        .collect::<BTreeSet<_>>();
    let exit_ids = events
        .iter()
        .filter(|event| event.kind == "EXIT_PROCESS")
        .filter_map(|event| event.process_id)
        .collect::<BTreeSet<_>>();
    process_ids == new_ids
        && process_ids == exit_ids
        && processes.iter().enumerate().all(|(index, process)| {
            events[index].kind == "NEW_PROCESS"
                && events[index].process_id == Some(process.process_id)
                && events[index + processes.len()].kind == "EXIT_PROCESS"
                && events[index + processes.len()].process_id == Some(process.process_id)
        })
}

fn persist_helper_variant(
    audit: &mut HelperAudit,
    mut variant: NativeHelperDiagnosticVariantResult,
) -> NativeHelperDiagnosticVariantResult {
    let process_trace_bytes = helper_process_trace_bytes(&variant);
    let job_trace_bytes = helper_job_trace_bytes(&variant);
    let home_change_bytes = helper_home_change_trace_bytes(&variant);
    let home_inventory_bytes = helper_home_inventory_bytes(&variant);
    let traces = [
        ("helper-process-trace.json", process_trace_bytes, 0usize),
        ("helper-job-trace.json", job_trace_bytes, 1usize),
        ("helper-home-change-trace.json", home_change_bytes, 2usize),
        ("helper-home-inventory.json", home_inventory_bytes, 3usize),
    ];
    for (name, bytes, slot) in traces {
        match create_new_artifact(
            &audit.runtime.output.join(name),
            &audit.runtime.output_directory,
            &bytes,
            Gate::CleanupFinalized,
        ) {
            Ok(artifact) => {
                if revalidate_created_artifact(&artifact).is_err() {
                    variant.record_blocker("HELPER_TRACE_ARTIFACT_REVALIDATION_FAILED");
                }
                match slot {
                    0 => {
                        variant.process_trace_path = Some(artifact.identity.path.clone());
                        variant.process_trace_sha256 = Some(artifact.identity.sha256.clone());
                        audit.process_trace = Some(artifact);
                    }
                    1 => {
                        variant.job_trace_path = Some(artifact.identity.path.clone());
                        variant.job_trace_sha256 = Some(artifact.identity.sha256.clone());
                        audit.job_trace = Some(artifact);
                    }
                    2 => {
                        variant.home_change_trace_path = Some(artifact.identity.path.clone());
                        variant.home_change_trace_sha256 = Some(artifact.identity.sha256.clone());
                        audit.home_change_trace = Some(artifact);
                    }
                    3 => {
                        variant.home_inventory_path = Some(artifact.identity.path.clone());
                        variant.home_inventory_sha256 = Some(artifact.identity.sha256.clone());
                        audit.home_inventory = Some(artifact);
                    }
                    _ => unreachable!(),
                }
            }
            Err(_) => variant.record_blocker("HELPER_TRACE_ARTIFACT_CREATE_NEW_FAILED"),
        }
    }
    let receipt_bytes = helper_variant_receipt_bytes(audit, &variant);
    let receipt_path = audit
        .runtime
        .run_root
        .join("helper-diagnostic-receipt.json");
    match create_new_artifact(
        &receipt_path,
        &audit.runtime.run_root_directory,
        &receipt_bytes,
        Gate::CleanupFinalized,
    ) {
        Ok(receipt) => {
            if revalidate_created_artifact(&receipt).is_err() {
                variant.record_blocker("HELPER_RECEIPT_REVALIDATION_FAILED");
                return variant;
            }
            variant.receipt_path = Some(receipt.identity.path.clone());
            variant.receipt_sha256 = Some(receipt.identity.sha256.clone());
        }
        Err(_) => variant.record_blocker("HELPER_RECEIPT_CREATE_NEW_FAILED"),
    }
    variant
}

fn revalidate_created_artifact(artifact: &CreatedArtifact) -> Result<(), Blocker> {
    let (volume, file_id, links, length) =
        file_id_from_handle(&artifact._file, Gate::CleanupFinalized)?;
    let sha256 = sha256_open_file(&artifact._file, Gate::CleanupFinalized)?;
    if volume != artifact.identity.volume_serial
        || file_id != artifact.identity.file_id
        || links != 1
        || length != artifact.identity.length
        || sha256 != artifact.identity.sha256
    {
        return Err(Blocker::new(
            Gate::CleanupFinalized,
            "HELPER_ARTIFACT_IDENTITY_CHANGED",
        ));
    }
    Ok(())
}

fn helper_process_trace_bytes(variant: &NativeHelperDiagnosticVariantResult) -> Vec<u8> {
    let processes = variant
        .processes
        .iter()
        .map(NativeHelperProcessEvidence::to_json)
        .collect::<Vec<_>>()
        .join(",");
    let debug_events = variant
        .debug_events
        .iter()
        .map(NativeHelperDebugEvent::to_json)
        .collect::<Vec<_>>()
        .join(",");
    format!(
        "{{\"schema\":\"pkm.codex.helper-process-trace.v1\",\"instrumentation\":\"DEBUG_PROCESS\",\"complete\":{},\"debug_lifecycle_events\":[{}],\"processes\":[{}]}}\n",
        json_bool(variant.debug_events_complete),
        debug_events,
        processes,
    )
    .into_bytes()
}

fn helper_job_trace_bytes(variant: &NativeHelperDiagnosticVariantResult) -> Vec<u8> {
    let events = variant
        .job_events
        .iter()
        .map(NativeHelperJobEvent::to_json)
        .collect::<Vec<_>>()
        .join(",");
    format!(
        "{{\"schema\":\"pkm.codex.helper-job-trace.v1\",\"complete\":{},\"job_role\":\"KILL_AND_DRAIN_BOUNDARY_ONLY\",\"active_process_limit_is_zero_child_proof\":false,\"active_process_limit\":{},\"limit_flags\":{},\"final_total_processes\":{},\"final_active_processes\":{},\"max_observed_total_processes\":{},\"max_observed_active_processes\":{},\"events\":[{}]}}\n",
        json_bool(variant.job_events_complete),
        variant.job_active_process_limit,
        variant.job_limit_flags,
        json_u32_or_null(variant.final_total_processes),
        json_u32_or_null(variant.final_active_processes),
        json_u32_or_null(variant.max_observed_total_processes),
        json_u32_or_null(variant.max_observed_active_processes),
        events,
    )
    .into_bytes()
}

fn helper_home_change_trace_bytes(variant: &NativeHelperDiagnosticVariantResult) -> Vec<u8> {
    let events = variant
        .codex_home_change_events
        .iter()
        .map(NativeHelperHomeChangeEvent::to_json)
        .collect::<Vec<_>>()
        .join(",");
    format!(
        "{{\"schema\":\"pkm.codex.helper-home-change-trace.v1\",\"scope\":\"CODEX_HOME_ONLY\",\"complete\":{},\"events\":[{}]}}\n",
        json_bool(variant.codex_home_change_trace_complete),
        events,
    )
    .into_bytes()
}

fn helper_home_inventory_bytes(variant: &NativeHelperDiagnosticVariantResult) -> Vec<u8> {
    let entries = variant
        .codex_home_inventory
        .iter()
        .map(NativeHelperHomeEntry::to_json)
        .collect::<Vec<_>>()
        .join(",");
    format!(
        "{{\"schema\":\"pkm.codex.helper-home-inventory.v1\",\"scope\":\"CODEX_HOME_ONLY\",\"complete\":{},\"entries\":[{}]}}\n",
        json_bool(variant.codex_home_inventory_complete),
        entries,
    )
    .into_bytes()
}

fn helper_variant_receipt_bytes(
    audit: &HelperAudit,
    variant: &NativeHelperDiagnosticVariantResult,
) -> Vec<u8> {
    let environment = audit
        .environment
        .iter()
        .map(|(key, value)| format!("\"{}\":\"{}\"", json_escape(key), json_escape(value)))
        .collect::<Vec<_>>()
        .join(",");
    format!(
        "{{\"schema\":\"pkm.codex.helper-diagnostic.variant.v1\",\"terminal\":\"BLOCKED_HELPER_DIAGNOSTIC\",\"production\":false,\"live\":false,\"auth_materialized_by_launcher\":false,\"network_requested\":false,\"network_usage\":\"UNOBSERVED\",\"credential_access\":\"UNOBSERVED\",\"external_file_access\":\"UNOBSERVED\",\"external_file_trace\":\"ETW_REQUIRED_NOT_RUN\",\"variant\":{},\"binary\":{},\"argv\":[\"--version\"],\"environment\":{{{}}},\"stdin\":{},\"prompt\":{},\"schema_artifact\":{},\"stdout\":{},\"stderr\":{},\"process_trace\":{},\"job_trace\":{},\"home_change_trace\":{},\"home_inventory\":{}}}\n",
        variant.to_json(),
        identity_json(audit.binary.as_ref()),
        environment,
        identity_json(audit.stdin.as_ref().map(|value| &value.identity)),
        identity_json(audit.prompt.as_ref().map(|value| &value.identity)),
        identity_json(audit.schema.as_ref().map(|value| &value.identity)),
        identity_json(audit.stdout.as_ref().map(|value| &value.identity)),
        identity_json(audit.stderr.as_ref().map(|value| &value.identity)),
        identity_json(audit.process_trace.as_ref().map(|value| &value.identity)),
        identity_json(audit.job_trace.as_ref().map(|value| &value.identity)),
        identity_json(
            audit
                .home_change_trace
                .as_ref()
                .map(|value| &value.identity),
        ),
        identity_json(
            audit
                .home_inventory
                .as_ref()
                .map(|value| &value.identity),
        ),
    )
    .into_bytes()
}

fn helper_result_failure(
    blocker: &'static str,
    variants: Vec<NativeHelperDiagnosticVariantResult>,
) -> NativeHelperDiagnosticResult {
    let mut result = base_result();
    result.blockers.insert(0, blocker);
    result.variants = variants;
    result
}

fn persist_helper_summary(
    variants: Vec<NativeHelperDiagnosticVariantResult>,
    release_blocker: Option<&'static str>,
) -> NativeHelperDiagnosticResult {
    let mut result = base_result();
    result.variants = variants;
    for blocker in result
        .variants
        .iter()
        .flat_map(|variant| variant.blockers.iter().copied())
    {
        if !result.blockers.contains(&blocker) {
            result.blockers.push(blocker);
        }
    }
    if let Some(blocker) = release_blocker {
        result.blockers.insert(0, blocker);
    }
    let complete = result.variants.len() == 4
        && result.variants[0].id == "B"
        && result.variants[1].id == "D"
        && result.variants[2].id == "E"
        && result.variants[3].id == "F"
        && result.variants.iter().all(|variant| {
            let blocker_contract_matches = if matches!(variant.id, "E" | "F") {
                variant.blocker == HELPER_NEVER_QUALIFIES
                    && variant.blockers == [HELPER_NEVER_QUALIFIES]
            } else {
                variant.blocker == HELPER_CONHOST_BLOCKER
                    && variant.blockers
                        == [
                            HELPER_NEVER_QUALIFIES,
                            HELPER_CONHOST_BLOCKER,
                            HELPER_JOB_LIMIT_OBSERVATION_BLOCKER,
                        ]
            };
            variant.receipt_path.is_some()
                && variant.receipt_sha256.is_some()
                && variant.process_trace_path.is_some()
                && variant.process_trace_sha256.is_some()
                && variant.job_trace_path.is_some()
                && variant.job_trace_sha256.is_some()
                && variant.home_change_trace_path.is_some()
                && variant.home_change_trace_sha256.is_some()
                && variant.home_inventory_path.is_some()
                && variant.home_inventory_sha256.is_some()
                && blocker_contract_matches
                && variant.debug_events_complete
                && variant.job_events_complete
                && variant.codex_home_change_trace_complete
                && variant.codex_home_inventory_complete
                && validate_variant_contract(variant, unsafe { GetCurrentProcessId() }).is_ok()
        });
    if !complete {
        result
            .blockers
            .insert(0, "HELPER_DIAGNOSTIC_EVIDENCE_INCOMPLETE");
    }
    if validate_result_contract(&result).is_err() {
        result
            .blockers
            .insert(0, "HELPER_DIAGNOSTIC_SUMMARY_CONTRACT_INVALID");
    }
    let runtime = match RuntimeAuthority::create_with_prefix(HELPER_SUMMARY_PREFIX) {
        Ok(value) => value,
        Err(error) => {
            result.blockers.insert(0, error.code);
            return result;
        }
    };
    let variants_json = result
        .variants
        .iter()
        .map(NativeHelperDiagnosticVariantResult::to_json)
        .collect::<Vec<_>>()
        .join(",");
    let blockers_json = result
        .blockers
        .iter()
        .map(|blocker| format!("\"{}\"", json_escape(blocker)))
        .collect::<Vec<_>>()
        .join(",");
    let bytes = format!(
        "{{\"schema\":\"pkm.codex.helper-diagnostic.summary.v1\",\"terminal\":\"BLOCKED_HELPER_DIAGNOSTIC\",\"production\":false,\"live\":false,\"network_usage\":\"UNOBSERVED\",\"credential_access\":\"UNOBSERVED\",\"external_file_access\":\"UNOBSERVED\",\"blockers\":[{}],\"variant_order\":[\"B\",\"D\",\"E\",\"F\"],\"variants\":[{}]}}\n",
        blockers_json, variants_json,
    )
    .into_bytes();
    let path = runtime.run_root.join("helper-diagnostic-summary.json");
    match create_new_artifact(
        &path,
        &runtime.run_root_directory,
        &bytes,
        Gate::CleanupFinalized,
    ) {
        Ok(receipt) => {
            if revalidate_created_artifact(&receipt).is_err() {
                result
                    .blockers
                    .insert(0, "HELPER_SUMMARY_REVALIDATION_FAILED");
                return result;
            }
            result.receipt_path = Some(receipt.identity.path.clone());
            result.receipt_sha256 = Some(receipt.identity.sha256.clone());
        }
        Err(_) => result
            .blockers
            .insert(0, "HELPER_SUMMARY_CREATE_NEW_FAILED"),
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn evidence_fixture() -> NativeHelperDiagnosticVariantResult {
        let mut variant = empty_variant(HELPER_POLICIES[0], HELPER_NEVER_QUALIFIES);
        let root_pid = 100u32;
        let helper_pid = 101u32;
        let binary_path = native_preflight_contract().binary_path;
        variant.child_created = true;
        variant.child_resumed = true;
        variant.descendants_zero = true;
        variant.exit_code = Some(0);
        variant.stdout_sha256 = Some(
            "sha256:47e8650c39eae3ea896e5873f03a97a65d183d81cd0d86616e8b83d7d87877ca".to_owned(),
        );
        variant.stderr_sha256 = Some(
            "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855".to_owned(),
        );
        variant.stdout_total_bytes = EXPECTED_CODEX_VERSION.len() + 1;
        variant.child_process_policy_readback = true;
        variant.child_process_policy_flags = 0;
        variant.job_limit_readback = true;
        variant.job_active_process_limit = 1;
        variant.job_limit_flags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        variant.final_total_processes = Some(2);
        variant.final_active_processes = Some(0);
        variant.max_observed_total_processes = Some(2);
        variant.max_observed_active_processes = Some(2);
        variant.processes = vec![
            NativeHelperProcessEvidence {
                process_id: root_pid,
                parent_process_id: 99,
                creation_time_100ns: 1,
                in_job: true,
                image_path: binary_path.clone(),
                image_volume_serial: 7,
                image_file_id: "01".repeat(16),
                image_link_count: 1,
                image_length: native_preflight_contract().binary_length,
                image_sha256: EXPECTED_CODEX_EXE_SHA256.to_owned(),
                image_role: HELPER_CODEX_IMAGE_ROLE,
                image_matches_pinned_binary: true,
                image_matches_pinned_conhost: false,
                image_signature: "FROZEN_OPENAI_AUTHENTICODE_VERIFIED",
                windows_build_binding: "UNQUALIFIED",
                command_line: format!("\"{}\" --version", binary_path.display()),
                command_line_matches_expected: true,
                command_line_source: HELPER_COMMAND_LINE_SOURCE,
            },
            NativeHelperProcessEvidence {
                process_id: helper_pid,
                parent_process_id: root_pid,
                creation_time_100ns: 2,
                in_job: true,
                image_path: PathBuf::from(FIXED_CONHOST_PATH),
                image_volume_serial: 8,
                image_file_id: "02".repeat(16),
                image_link_count: 2,
                image_length: FIXED_CONHOST_LENGTH,
                image_sha256: FIXED_CONHOST_SHA256.to_owned(),
                image_role: HELPER_CONHOST_IMAGE_ROLE,
                image_matches_pinned_binary: false,
                image_matches_pinned_conhost: true,
                image_signature: "UNOBSERVED",
                windows_build_binding: "UNQUALIFIED",
                command_line: FIXED_CONHOST_COMMAND_LINE.to_owned(),
                command_line_matches_expected: true,
                command_line_source: HELPER_COMMAND_LINE_SOURCE,
            },
        ];
        variant.debug_events_complete = true;
        variant.debug_events = vec![
            NativeHelperDebugEvent {
                sequence: 0,
                kind: "CREATE_PROCESS",
                process_id: root_pid,
                thread_id: 200,
            },
            NativeHelperDebugEvent {
                sequence: 1,
                kind: "CREATE_PROCESS",
                process_id: helper_pid,
                thread_id: 201,
            },
            NativeHelperDebugEvent {
                sequence: 2,
                kind: "EXIT_PROCESS",
                process_id: root_pid,
                thread_id: 200,
            },
            NativeHelperDebugEvent {
                sequence: 3,
                kind: "EXIT_PROCESS",
                process_id: helper_pid,
                thread_id: 201,
            },
        ];
        variant.job_events_complete = true;
        variant.job_events = vec![
            NativeHelperJobEvent {
                sequence: 0,
                kind: "NEW_PROCESS",
                process_id: Some(root_pid),
            },
            NativeHelperJobEvent {
                sequence: 1,
                kind: "NEW_PROCESS",
                process_id: Some(helper_pid),
            },
            NativeHelperJobEvent {
                sequence: 2,
                kind: "EXIT_PROCESS",
                process_id: Some(root_pid),
            },
            NativeHelperJobEvent {
                sequence: 3,
                kind: "EXIT_PROCESS",
                process_id: Some(helper_pid),
            },
            NativeHelperJobEvent {
                sequence: 4,
                kind: "ACTIVE_PROCESS_ZERO",
                process_id: None,
            },
        ];
        variant.codex_home_change_trace_complete = true;
        variant.codex_home_change_events = vec![NativeHelperHomeChangeEvent {
            sequence: 0,
            action: "ADDED",
            relative_path: "tmp".to_owned(),
        }];
        variant.codex_home_inventory_complete = true;
        variant.codex_home_inventory = vec![NativeHelperHomeEntry {
            relative_path: "tmp".to_owned(),
            kind: "directory",
            reparse_point: false,
            volume_serial: 7,
            file_id: "02".repeat(16),
            parent_file_id: "03".repeat(16),
            link_count: 1,
            length: 0,
            sha256: None,
        }];
        variant.record_blocker(HELPER_CONHOST_BLOCKER);
        variant.record_blocker(HELPER_JOB_LIMIT_OBSERVATION_BLOCKER);
        variant
    }

    #[test]
    fn missing_events_overflow_pid_reuse_extra_process_and_cleanup_failure_are_blocked() {
        let exact = evidence_fixture();
        assert_eq!(validate_variant_contract(&exact, 99), Ok(()));

        let mut missing_event = exact.clone();
        missing_event.job_events.remove(1);
        assert!(validate_variant_contract(&missing_event, 99).is_err());

        let mut overflow = exact.clone();
        overflow.codex_home_change_trace_complete = false;
        assert!(validate_variant_contract(&overflow, 99).is_err());

        let mut pid_reuse = exact.clone();
        pid_reuse.processes[1].process_id = pid_reuse.processes[0].process_id;
        assert!(validate_variant_contract(&pid_reuse, 99).is_err());

        let mut creation_time_reuse = exact.clone();
        creation_time_reuse.processes[1].creation_time_100ns =
            creation_time_reuse.processes[0].creation_time_100ns;
        assert!(validate_variant_contract(&creation_time_reuse, 99).is_err());

        let mut extra_process = exact.clone();
        extra_process
            .processes
            .push(extra_process.processes[1].clone());
        assert!(validate_variant_contract(&extra_process, 99).is_err());

        let mut cleanup_failure = exact;
        cleanup_failure.descendants_zero = false;
        assert!(validate_variant_contract(&cleanup_failure, 99).is_err());
    }

    #[test]
    fn helper_json_serializes_unsafe_memory_state_instead_of_hiding_it() {
        let mut result = base_result();
        result.production = true;
        assert!(result.to_json().contains("\"production\":true"));
        assert!(validate_result_contract(&result).is_err());

        let mut variant = evidence_fixture();
        variant.live = true;
        assert!(variant.to_json().contains("\"live\":true"));
        assert!(validate_variant_contract(&variant, 99).is_err());
    }

    #[test]
    fn observed_conhost_is_independent_evidence_and_an_unavoidable_hard_blocker() {
        let variant = evidence_fixture();
        assert_eq!(variant.processes[0].image_role, "PINNED_CODEX_ROOT");
        assert_eq!(
            variant.processes[1].image_role,
            "FIXED_CONTENT_CONHOST_DESCENDANT"
        );
        assert!(variant.processes[1].image_matches_pinned_conhost);
        assert!(!variant.processes[1].image_matches_pinned_binary);
        assert!(variant.blockers.contains(&HELPER_CONHOST_BLOCKER));
        assert!(variant
            .blockers
            .contains(&HELPER_JOB_LIMIT_OBSERVATION_BLOCKER));
        assert_eq!(variant.debug_events.len(), 4);
        assert_eq!(validate_variant_contract(&variant, 99), Ok(()));
    }

    #[test]
    fn parallel_watcher_image_inventory_and_attempt_failures_are_never_overwritten() {
        let mut watcher_and_image = empty_variant(HELPER_POLICIES[0], HELPER_NEVER_QUALIFIES);
        watcher_and_image.record_blocker("HELPER_HOME_WATCH_OVERFLOW");
        watcher_and_image.record_blocker("HELPER_DIAGNOSTIC_IMAGE_OR_COMMAND_LINE_MISMATCH");
        assert_eq!(watcher_and_image.blocker, "HELPER_HOME_WATCH_OVERFLOW");
        assert!(watcher_and_image
            .blockers
            .contains(&"HELPER_DIAGNOSTIC_IMAGE_OR_COMMAND_LINE_MISMATCH"));

        let mut inventory_and_attempt = empty_variant(HELPER_POLICIES[1], HELPER_NEVER_QUALIFIES);
        inventory_and_attempt.record_blocker("HELPER_HOME_INVENTORY_REPARSE_POINT_FORBIDDEN");
        inventory_and_attempt.record_blocker("HELPER_OUTPUT_LIMIT_EXCEEDED");
        assert_eq!(
            inventory_and_attempt.blocker,
            "HELPER_HOME_INVENTORY_REPARSE_POINT_FORBIDDEN"
        );
        assert!(inventory_and_attempt
            .blockers
            .contains(&"HELPER_OUTPUT_LIMIT_EXCEEDED"));
        let json = inventory_and_attempt.to_json();
        assert!(json.contains("HELPER_HOME_INVENTORY_REPARSE_POINT_FORBIDDEN"));
        assert!(json.contains("HELPER_OUTPUT_LIMIT_EXCEEDED"));
    }

    #[test]
    fn half_rename_is_never_called_a_complete_home_change_trace() {
        let events = vec![NativeHelperHomeChangeEvent {
            sequence: 0,
            action: "RENAMED_OLD_NAME",
            relative_path: "old".to_owned(),
        }];
        assert!(validate_home_change_trace(&events).is_err());
    }

    #[test]
    fn directory_watcher_only_treats_wait_timeout_as_a_bounded_poll() {
        assert!(is_directory_watch_poll_timeout(WAIT_TIMEOUT));
        assert!(!is_directory_watch_poll_timeout(1_460)); // ERROR_TIMEOUT, not a wait timeout.
        assert!(!is_directory_watch_poll_timeout(ERROR_OPERATION_ABORTED));
    }

    #[test]
    fn directory_watcher_finishes_after_a_racing_successful_completion_when_stop_is_set() {
        assert!(directory_watcher_should_finish_after_completion(true));
        assert!(!directory_watcher_should_finish_after_completion(false));
    }

    #[test]
    fn failure_chain_preserves_primary_and_cleanup_codes_without_duplicates() {
        let mut codes = Vec::new();
        record_failure_code(&mut codes, "PRIMARY_SECURITY_FAILURE");
        record_failure_code(&mut codes, "CLEANUP_FAILURE");
        record_failure_code(&mut codes, "PRIMARY_SECURITY_FAILURE");

        assert_eq!(codes, ["PRIMARY_SECURITY_FAILURE", "CLEANUP_FAILURE"]);
    }
}
