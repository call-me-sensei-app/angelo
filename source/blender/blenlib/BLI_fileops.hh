/* SPDX-FileCopyrightText: 2001-2002 NaN Holding BV. All rights reserved.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bli
 * \brief File and directory operations.
 */

#pragma once

#include <fstream>
#include <stdint.h>
#include <stdio.h>
#include <string>
#include <sys/stat.h>

/* for size_t (needed on windows) */
#include <stddef.h>

#include <limits.h> /* for PATH_MAX */

#include "BLI_compiler_attrs.hh"
#include "BLI_enum_flags.hh"
#include "BLI_fileops_types.hh"
#include "BLI_string_ref.hh"

#include <cstdint>
namespace blender {

#ifndef PATH_MAX
#  define PATH_MAX 4096
#endif

/* -------------------------------------------------------------------- */
/** \name Common
 * \{ */

/**
 * Returns true if the path (file or directory) exists.
 */
bool BLI_exists(const char *path) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();

/**
 * Returns the st_mode from stat-ing the specified path name, or 0 if stat fails
 * (most likely doesn't exist or no access).
 */
int BLI_file_stat_mode(const char *path) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();

/**
 * \return 0 on success.
 */
int BLI_copy(const char *path_src, const char *path_dst) ATTR_NONNULL();
/**
 * When `path_src` points to a directory, moves all its contents into `path_dst`,
 * else rename `path_src` itself to `path_dst`.
 * \return 0 on success.
 */
int BLI_path_move(const char *path_src, const char *path_dst) ATTR_NONNULL();

/**
 * Rename a file or directory, unless `to` already exists.
 *
 * \note This matches Windows `rename` logic, _not_ Unix one. It does not allow to replace an
 * existing target. Use #BLI_rename_overwrite instead if existing file should be replaced.
 *
 * \param from: The path to rename from (return failure if it does not exist).
 * \param to: The destination path.
 * \return zero on success (matching 'rename' behavior).
 */
int BLI_rename(const char *from, const char *to) ATTR_NONNULL();

/**
 * Rename a file or directory, replacing target `to` path if it exists.
 *
 * \note This matches Unix `rename` logic. It does allow to replace an existing target. Use
 * #BLI_rename instead if existing file should never be replaced. However, if `to` is an existing,
 * non-empty directory, the operation will fail.
 *
 * \note There is still no feature-parity between behaviors on Windows and Unix, in case the target
 * `to` exists and is opened by some process in the system:
 *   - On Unix, it will typically succeed
 *     (see https://man7.org/linux/man-pages/man2/rename.2.html for details).
 *   - On Windows, it will always fail
 *     (see https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw for
 *      details).
 *
 * \warning Due to internal limitation/implementation, on Windows, in case paths point to
 * directories, it's up to the caller to ensure that `from` and `to` are not the same directory.
 * Since `to` is being deleted to make room for `from`, this will result in `from` being deleted as
 * well.
 *
 * See #BLI_path_move to move directories.
 *
 * \param from: The path to rename from (return failure if it does not exist).
 * \param to: The destination path.
 * This will be deleted if it already exists, unless it's a directory which will fail.
 * \return zero on success (matching 'rename' behavior).
 */
int BLI_rename_overwrite(const char *from, const char *to) ATTR_NONNULL();
/**
 * Deletes the specified file or directory.
 *
 * \param dir: Delete an empty directory instead of a file.
 * The value is ignored when `recursive` is true but should true to make the intention clear.
 * If the directory is not empty, delete fails.
 * \param recursive: Recursively delete files including `path` which may be a directory of a file.
 *
 * \note Symbolic-Links for (UNIX) behave as follows:
 * - Never followed, treated as regular files.
 * - Links are removed, not the files/directories they references.
 * - When `path` itself links to another directory,
 *   deleting `path` behaves as if a regular file is being deleted.
 * - If `dir` is true and `path` is a link, delete fails.
 *
 * \return zero on success (matching 'remove' behavior).
 */
/**
 * The confinement's file gate (Angelo, doc/angelo/script_confinement.rst): while it is set, every open, rename,
 * delete, copy, touch and folder creation here asks `refused(path, write)` first and fails with `EACCES` when it
 * says so. `depth`/`enter`/`leave` carry a confined thread's state to a job thread it starts (`WM_jobs_start`).
 */
struct BLI_FileGate {
  bool (*refused)(const char *path, bool write);
  int (*depth)();
  void (*enter)(int depth);
  void (*leave)(int depth);
  /** Whether a text may be written to a .blend still marked to run when the file opens (`Text *`): false for a
   * text a confined thread made or changed so (the confinement's snapshot). Null: always. */
  bool (*load_time_kept)(const void *text);
  /** Whether a driver (`FCurve *`) whose expression needs Python may be written as it is; false: written with its
   * Python off (`DRIVER_FLAG_ANGELO_UNTRUSTED`). Null: always. */
  bool (*driver_kept)(const void *fcurve);
  /** Whether a Freestyle style module (`FreestyleModuleConfig *`) may be written switched on. Null: always. */
  bool (*style_module_kept)(const void *module);
  /** Whether an agent script is running on this thread (the confinement's one test). Null: never. */
  bool (*confined)();
  /** Whether the person trusted this exact content (`const Text *`) to run when a file opens or as a style module:
   * a digest the person's own action recorded (doc/angelo/script_confinement.rst). Null: always. */
  bool (*autorun_trusted)(const void *text);
  /** The person's own action (never a confined thread's): trust this text's content (`Text *`) from now on. */
  void (*autorun_trust)(void *text);
  /** Round 3, "the data decides": a data-block got its session uid on a confined thread. `source_file`: the .blend
   * it was read from (an open, append or link), null when it was made or copied: everything it holds is the
   * script's when it was made, or its file is script-made. Null: nothing recorded. */
  void (*script_id_made)(unsigned int session_uid, const char *source_file);
  /** A confined thread set the property `identifier` of data of the RNA type `struct_type` (a `const StructRNA *`,
   * the data's refined type) in the data-block `owner_session_uid`. */
  void (*script_property_set)(unsigned int owner_session_uid, const void *struct_type, const char *identifier);
};
void BLI_fileops_gate_set(const BLI_FileGate *gate);
const BLI_FileGate *BLI_fileops_gate_get();

int BLI_delete(const char *path, bool dir, bool recursive) ATTR_NONNULL();
/**
 * Soft deletes the specified file or directory (depending on dir) by moving the files to the
 * recycling bin, optionally doing recursive delete of directory contents.
 *
 * \return zero on success (matching 'remove' behavior).
 */
int BLI_delete_soft(const char *filepath, const char **r_error_message) ATTR_NONNULL();
#if 0 /* Unused */
int BLI_create_symlink(const char *path, const char *path_dst) ATTR_NONNULL();
#endif

/* Keep in sync with the definition of struct `direntry` in `BLI_fileops_types.hh`. */
#ifdef WIN32
#  if defined(_MSC_VER)
typedef struct _stat64 BLI_stat_t;
#  else
typedef struct _stat BLI_stat_t;
#  endif
#else
typedef struct stat BLI_stat_t;
#endif

int BLI_fstat(int fd, BLI_stat_t *buffer) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
int BLI_stat(const char *path, BLI_stat_t *buffer) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
int64_t BLI_ftell(FILE *stream) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
int BLI_fseek(FILE *stream, int64_t offset, int whence);
int64_t BLI_lseek(int fd, int64_t offset, int whence);

#ifdef WIN32
int BLI_wstat(const wchar_t *path, BLI_stat_t *buffer);
#endif

enum eFileAttributes {
  FILE_ATTR_READONLY = 1 << 0,        /* Read-only or Immutable. */
  FILE_ATTR_HIDDEN = 1 << 1,          /* Hidden or invisible. */
  FILE_ATTR_SYSTEM = 1 << 2,          /* Used by the Operating System. */
  FILE_ATTR_ARCHIVE = 1 << 3,         /* Marked as archived. */
  FILE_ATTR_COMPRESSED = 1 << 4,      /* Compressed. */
  FILE_ATTR_ENCRYPTED = 1 << 5,       /* Encrypted. */
  FILE_ATTR_RESTRICTED = 1 << 6,      /* Protected by OS. */
  FILE_ATTR_TEMPORARY = 1 << 7,       /* Used for temporary storage. */
  FILE_ATTR_SPARSE_FILE = 1 << 8,     /* Sparse File. */
  FILE_ATTR_OFFLINE = 1 << 9,         /* Contents available after a short delay. */
  FILE_ATTR_ALIAS = 1 << 10,          /* Mac Alias or Windows LNK. File-based redirection. */
  FILE_ATTR_REPARSE_POINT = 1 << 11,  /* File has associated re-parse point. */
  FILE_ATTR_SYMLINK = 1 << 12,        /* Reference to another file. */
  FILE_ATTR_JUNCTION_POINT = 1 << 13, /* Folder Symbolic-link. */
  FILE_ATTR_MOUNT_POINT = 1 << 14,    /* Volume mounted as a folder. */
  FILE_ATTR_HARDLINK = 1 << 15,       /* Duplicated directory entry. */
};
ENUM_OPERATORS(eFileAttributes);

#define FILE_ATTR_ANY_LINK \
  (FILE_ATTR_ALIAS | FILE_ATTR_REPARSE_POINT | FILE_ATTR_SYMLINK | FILE_ATTR_JUNCTION_POINT | \
   FILE_ATTR_MOUNT_POINT | FILE_ATTR_HARDLINK)

/** \} */

/* -------------------------------------------------------------------- */
/** \name External File Operations
 * \{ */

enum FileExternalOperation {
  FILE_EXTERNAL_OPERATION_OPEN = 1,
  FILE_EXTERNAL_OPERATION_FOLDER_OPEN,
  /* Following are Windows-only: */
  FILE_EXTERNAL_OPERATION_EDIT,
  FILE_EXTERNAL_OPERATION_NEW,
  FILE_EXTERNAL_OPERATION_FIND,
  FILE_EXTERNAL_OPERATION_SHOW,
  FILE_EXTERNAL_OPERATION_PLAY,
  FILE_EXTERNAL_OPERATION_BROWSE,
  FILE_EXTERNAL_OPERATION_PREVIEW,
  FILE_EXTERNAL_OPERATION_PRINT,
  FILE_EXTERNAL_OPERATION_INSTALL,
  FILE_EXTERNAL_OPERATION_RUNAS,
  FILE_EXTERNAL_OPERATION_PROPERTIES,
  FILE_EXTERNAL_OPERATION_FOLDER_FIND,
  FILE_EXTERNAL_OPERATION_FOLDER_CMD,
};

bool BLI_file_external_operation_supported(const char *filepath, FileExternalOperation operation);
bool BLI_file_external_operation_execute(const char *filepath, FileExternalOperation operation);

/** \} */

/* -------------------------------------------------------------------- */
/** \name Directories
 * \{ */

struct direntry;

/**
 * Does the specified path point to a directory?
 * \note Would be better in `fileops.cc` except that it needs `stat.h` so add here.
 */
bool BLI_is_dir(const char *path) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
/**
 * Does the specified path point to a non-directory?
 */
bool BLI_is_file(const char *path) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
/**
 * \return true on success (i.e. given path now exists on FS), false otherwise.
 */
bool BLI_dir_create_recursive(const char *dirname) ATTR_NONNULL();
/**
 * Returns the number of free bytes on the volume containing the specified path.
 *
 * \note Not actually used anywhere.
 */
double BLI_dir_free_space(const char *dir) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
/**
 * Copies the current working directory into *dir (max size maxncpy), and
 * returns a pointer to same.
 *
 * \note can return NULL when the size is not big enough
 */
char *BLI_current_working_dir(char *dir, size_t maxncpy) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();

/**
 * Get the user's home directory, i.e.
 * - Unix: `$HOME` or #passwd::pw_dir.
 * - Windows: `%userprofile%`
 *
 * \return The home directory or null when it cannot be accessed.
 *
 * \note By convention, failure to access home means any derived directories fail as well
 * instead of attempting to create a fallback such as `/`, `/tmp`, `C:\` ... etc.
 * Although there may be rare cases where a fallback is appropriate.
 */
const char *BLI_dir_home();

eFileAttributes BLI_file_attributes(const char *path);
/**
 * Changes the current working directory to the provided path.
 *
 * Usage of this function is strongly discouraged as it is not thread safe. It will likely cause
 * issues if there is an operation on another thread that does not expect the current working
 * directory to change. This has been added to support USDZ export, which has a problematic
 * "feature" described in this issue #99807. It will be removed if it is possible to resolve
 * that issue upstream in the USD library.
 *
 * \return true on success, false otherwise.
 */
bool BLI_change_working_dir(const char *dir);

/** \} */

/* -------------------------------------------------------------------- */
/** \name File-List
 * \{ */

/**
 * Scans the contents of the directory named `dirname`, and allocates and fills in an
 * array of entries describing them in `r_filelist`.
 *
 * \return The length of `r_filelist` array.
 */
unsigned int BLI_filelist_dir_contents(const char *dirname, struct direntry **r_filelist);
/**
 * Deep-duplicate of a single direntry.
 */
void BLI_filelist_entry_duplicate(struct direntry *dst, const struct direntry *src);
/**
 * Deep-duplicate of a #direntry array including the array itself.
 */
void BLI_filelist_duplicate(struct direntry **dest_filelist,
                            struct direntry *const src_filelist,
                            unsigned int nrentries);
/**
 * Frees storage for a single direntry, not the direntry itself.
 */
void BLI_filelist_entry_free(struct direntry *entry);
/**
 * Frees storage for an array of #direntry, including the array itself.
 */
void BLI_filelist_free(struct direntry *filelist, unsigned int nrentries);

/**
 * Convert given entry's size into human-readable strings.
 */
void BLI_filelist_entry_size_to_string(const struct stat *st,
                                       uint64_t st_size_fallback,
                                       bool compact,
                                       char r_size[FILELIST_DIRENTRY_SIZE_LEN]);
/**
 * Convert given entry's modes into human-readable strings.
 */
void BLI_filelist_entry_mode_to_string(const struct stat *st,
                                       bool compact,
                                       char r_mode1[FILELIST_DIRENTRY_MODE_LEN],
                                       char r_mode2[FILELIST_DIRENTRY_MODE_LEN],
                                       char r_mode3[FILELIST_DIRENTRY_MODE_LEN]);
/**
 * Convert given entry's owner into human-readable strings.
 */
void BLI_filelist_entry_owner_to_string(const struct stat *st,
                                        bool compact,
                                        char r_owner[FILELIST_DIRENTRY_OWNER_LEN]);

/** \} */

/* -------------------------------------------------------------------- */
/** \name Files
 * \{ */

FILE *BLI_fopen(const char *filepath, const char *mode) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
void *BLI_gzopen(const char *filepath, const char *mode) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
int BLI_open(const char *filepath, int oflag, int pmode) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
/**
 * Create `filepath` as a new, empty file for writing, first removing whatever entry already has
 * that name without following it. A stale file, a symbolic link or a hard link at the name is
 * unlinked (the link itself, never what it points at), then the file is created exclusively,
 * refusing to open through a link that reappears at the name in between.
 *
 * Use this instead of `O_CREAT | O_TRUNC` for a predictable temporary name next to a target
 * (such as `<file>@` while saving a .blend file): anyone able to create files in that folder
 * could otherwise plant the name as a link to another file the user can write, and the save
 * would truncate and overwrite that file instead.
 *
 * \param oflag: access and mode flags (`O_WRONLY` or `O_RDWR`, optionally `O_BINARY`).
 * Creation flags are supplied here; `O_TRUNC` is ignored because the file is always new.
 * \return a descriptor as #BLI_open does, or -1 with `errno` set. `EEXIST` means an entry
 * that could not be removed (a directory, for example) or that reappeared is at the name.
 */
int BLI_open_replacing(const char *filepath, int oflag, int pmode) ATTR_WARN_UNUSED_RESULT
    ATTR_NONNULL();
/**
 * Make what was written to the open file `file` durable: when this returns 0, the data is on
 * the storage device, so a power loss or system crash cannot leave the file shorter than what
 * was written. Call it before renaming a temporary file over the one it replaces.
 *
 * macOS uses `F_FULLFSYNC`, because `fsync` there may leave the data in the drive's volatile
 * cache; only a file system that does not support it (#BLI_file_sync_error_is_unsupported) gets
 * `fsync` instead. Any other `F_FULLFSYNC` error is returned: an NFS client reports a failed
 * write-back once and then forgets it, so a second call could report success for lost data.
 * Windows uses `FlushFileBuffers`.
 *
 * \return 0 on success, -1 with `errno` set. #BLI_file_sync_error_is_unsupported tells a file
 * system that cannot sync at all from one that lost data (`EIO`, `ENOSPC`, `EDQUOT`, ...).
 */
int BLI_file_sync(int file) ATTR_WARN_UNUSED_RESULT;
/**
 * Order what was written to the open file `file` before anything written after this returns:
 * the data has reached the storage device, though it may still be in the drive's volatile cache.
 * Call it before renaming a temporary file over the one it replaces: the rename cannot reach the
 * device before the data, so a power loss leaves the old file or the new one, never a truncated
 * one. A later #BLI_dir_sync of the folder makes both durable with one cache flush.
 *
 * macOS uses `F_BARRIERFSYNC`, falling back to `F_FULLFSYNC` and then `fsync` only where a file
 * system rejects it (some devices without barriers turn it into a full flush themselves). Other
 * platforms have no cheaper ordered sync and do what #BLI_file_sync does.
 *
 * \return As #BLI_file_sync.
 */
int BLI_file_sync_barrier(int file) ATTR_WARN_UNUSED_RESULT;
/**
 * Whether `error`, the `errno` of a failed #BLI_file_sync, #BLI_file_sync_barrier or
 * #BLI_dir_sync, means the file system cannot sync at all (some network, FUSE or virtual drives),
 * rather than that written data may be lost.
 */
bool BLI_file_sync_error_is_unsupported(int error);
/** One way to sync an open file, as `fsync` does: 0, or -1 with `errno` set. */
using BLIFileSyncFn = int (*)(int file);
/**
 * The fallback rule of #BLI_file_sync and #BLI_file_sync_barrier: run `attempts` in order, from
 * the strongest sync to the weakest, moving to the next only when one fails with an error
 * #BLI_file_sync_error_is_unsupported accepts. Any other error is returned at once.
 */
int BLI_file_sync_with_fallback(int file,
                                const BLIFileSyncFn attempts[],
                                int attempts_num) ATTR_WARN_UNUSED_RESULT;
/**
 * Make the entries of the folder `dirpath` durable, such as a file just renamed into it, so the
 * rename survives a power loss. On macOS this is a full `F_FULLFSYNC`, which also flushes data
 * that #BLI_file_sync_barrier ordered. Windows has no folder sync and returns 0: its rename can
 * still be lost to a power loss (bringing back the previous version), but a file flushed before
 * it is never truncated.
 *
 * \return 0 on success, -1 with `errno` set.
 */
int BLI_dir_sync(const char *dirpath) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
int BLI_access(const char *filepath, int mode) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();

/**
 * A version of `read` with the following differences:
 * - continues reading until failure or the requested size is met.
 * - Reads `size_t` bytes instead of `int` on WIN32.
 * \return the number of bytes read.
 */
int64_t BLI_read(int fd, void *buf, size_t nbytes);

/**
 * Returns true if the file with the specified name can be written.
 * This implementation uses access(2), which makes the check according
 * to the real UID and GID of the process, not its effective UID and GID.
 * This shouldn't matter for Blender, which is not going to run privileged anyway.
 */
bool BLI_file_is_writable(const char *filepath) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
/**
 * Creates the file with nothing in it, or updates its last-modified date if it already exists.
 * Returns true if successful (like the unix touch command).
 */
bool BLI_file_touch(const char *filepath) ATTR_NONNULL(1);
/**
 * Ensures that the parent directory of `filepath` exists.
 *
 * \return true on success (i.e. given path now exists on file-system), false otherwise.
 */
bool BLI_file_ensure_parent_dir_exists(const char *filepath) ATTR_NONNULL(1);

/**
 * Return alias/shortcut file target.
 * \param filepath: The source of the alias.
 * \param r_targetpath: Buffer for the target path an alias points to.
 *
 * \return true when an alias was found and set.
 *
 * \note This is only used on APPLE/WIN32.
 */
bool BLI_file_alias_target(const char *filepath,
                           char r_targetpath[/*FILE_MAXDIR*/ 768]) ATTR_WARN_UNUSED_RESULT;

bool BLI_file_magic_is_gzip(const char header[4]);

size_t BLI_file_zstd_from_mem_at_pos(void *buf,
                                     size_t len,
                                     FILE *file,
                                     size_t file_offset,
                                     int compression_level) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
size_t BLI_file_unzstd_to_mem_at_pos(void *buf, size_t len, FILE *file, size_t file_offset)
    ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();
bool BLI_file_magic_is_zstd(const char header[4]);

/**
 * Returns the file size of an opened file descriptor or `size_t(-1)` on failure.
 */
size_t BLI_file_descriptor_size(int file) ATTR_WARN_UNUSED_RESULT;
/**
 * Angelo: whether \a file is an open descriptor of this process.
 *
 * Ask this before the first use of a descriptor a caller supplied (Python, another module). On
 * Windows the C runtime ends the process when `_fstat64`, `_read`, `_write`, `_lseeki64`, `_dup`,
 * `_close` or `_get_osfhandle` is given a descriptor that is negative, out of range or closed,
 * where POSIX returns `EBADF`. This never ends the process and leaves `errno` as it was. (A
 * debug Windows build turns the runtime's assertion report off for the lookup, for the whole
 * process, one caller at a time: an assertion of another thread in that moment is not shown.)
 * It is a check, not a hold: a descriptor its owner closes afterwards is that owner's error.
 * `tests/python/angelo_agent/test_native_descriptor_checks.py` reads the sources for uses it can
 * recognise (it says which); it is an aid, not a proof that every path asks first.
 */
bool BLI_file_descriptor_is_open(int file) ATTR_WARN_UNUSED_RESULT;
/**
 * Returns the size of a file or `size_t(-1)` on failure..
 */
size_t BLI_file_size(const char *path) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();

/**
 * Compare if one was last modified before the other.
 *
 * \return true when is `file1` older than `file2`.
 */
bool BLI_file_older(const char *file1, const char *file2) ATTR_WARN_UNUSED_RESULT ATTR_NONNULL();

/**
 * Reads the contents of a text file.
 *
 * \return the lines in a linked list (an empty list when file reading fails).
 */
struct LinkNode *BLI_file_read_as_lines(const char *filepath) ATTR_WARN_UNUSED_RESULT
    ATTR_NONNULL();

/**
 * Read the contents of `fp`, returning the result as a buffer or null when it can't be read.
 *
 * \param r_size: The size of the file contents read into the buffer (excluding `pad_bytes`).
 */
void *BLI_file_read_data_as_mem_from_handle(FILE *fp,
                                            bool read_size_exact,
                                            size_t pad_bytes,
                                            size_t *r_size);

char *BLI_file_read_text_as_mem(const char *filepath, size_t pad_bytes, size_t *r_size);
/**
 * Return the text file data with:
 *
 * - Newlines replaced with '\0'.
 * - Optionally trim white-space, replacing trailing <space> & <tab> with '\0'.
 *
 * This is an alternative to using #BLI_file_read_as_lines,
 * allowing us to loop over lines without converting it into a linked list
 * with individual allocations.
 *
 * \param trim_trailing_space: Replace trailing spaces & tabs with nil.
 * This arguments prevents the caller from counting blank lines (if that's important).
 * \param pad_bytes: When this is non-zero, the first byte is set to nil,
 * to simplify parsing the file.
 * It's recommended to pass in 1, so all text is nil terminated.
 * \param r_size: The size of the file contents read into the buffer (excluding `pad_bytes`).
 *
 * Example looping over lines:
 *
 * \code{.c}
 * size_t data_len;
 * char *data = BLI_file_read_text_as_mem_with_newline_as_nil(filepath, true, 1, &data_len);
 * char *data_end = data + data_len;
 * for (char *line = data; line != data_end; line = strlen(line) + 1) {
 *  printf("line='%s'\n", line);
 * }
 * \endcode
 */
char *BLI_file_read_text_as_mem_with_newline_as_nil(const char *filepath,
                                                    bool trim_trailing_space,
                                                    size_t pad_bytes,
                                                    size_t *r_size);
void *BLI_file_read_binary_as_mem(const char *filepath, size_t pad_bytes, size_t *r_size);
/**
 * Frees memory from a previous call to #BLI_file_read_as_lines.
 */
void BLI_file_free_lines(struct LinkNode *lines);

/* This weirdo pops up in two places. */
#if !defined(WIN32)
#  ifndef O_BINARY
#    define O_BINARY 0
#  endif
#else
void BLI_get_short_name(char short_name[256], const char *filepath);
#endif

/** \} */

/**
 * Angelo: a file's identity as CPython's `os.stat` reports it, so Python can compare it with its
 * own stat of that file later: `st_dev` (`device`; `device_signed` for `NODEV`), `st_ino`
 * (`(index_high << 64) | index_low`), `st_size`, `st_mtime_ns` and, on macOS, `st_birthtime_ns`
 * (none on Windows). On Windows that is the 64-bit volume serial of `FILE_ID_INFO` where the
 * volume answers it, and its 128-bit file ID where that is nonzero (else the 64-bit file index),
 * as CPython 3.13 takes them; without `FILE_ID_INFO`, the 32-bit serial and the file index.
 */
struct FileIdentity {
  uint64_t device = 0;
  bool device_signed = false;
  uint64_t index_low = 0;
  uint64_t index_high = 0;
  int64_t size = 0;
  int64_t modified_ns = 0;
  int64_t created_ns = 0;
};

/**
 * The identity of the file at `filepath` (never following a link at its name); false when it
 * can't be asked.
 */
bool BLI_file_identity(const char *filepath, FileIdentity *r_identity);

/**
 * The file at `filepath` held open (never following a link at its name), so its identity can be
 * taken after it is renamed (#BLI_file_identity_held): a rename keeps the file, not necessarily
 * its identity on every file system (FAT on macOS numbers a file by its directory entry). On
 * Windows the handle shares delete, so the file can still be renamed. Null when it can't be
 * opened.
 */
void *BLI_file_identity_hold(const char *filepath);
/** The identity of a held file now; false for a null `held` or when it can't be asked. */
bool BLI_file_identity_held(const void *held, FileIdentity *r_identity);
/**
 * Whether a held file is known to be at no name any more: unlinked, or replaced by another file
 * renamed over it (on Windows: deleted, or delete pending). False for a null `held` or when it
 * can't be asked. Unlike its identity, this never depends on how a file system numbers files.
 */
bool BLI_file_identity_held_unlinked(const void *held);
/** Close a held file (null: nothing). */
void BLI_file_identity_release(void *held);
/**
 * The identity of the file open as the C runtime descriptor `file` (a writer's, before it closes
 * it); false when it can't be asked.
 */
bool BLI_file_identity_descriptor(int file, FileIdentity *r_identity);
/** Whether `a` and `b` name the same file (device and file ID; never its size or times). */
bool BLI_file_identity_same_file(const FileIdentity &a, const FileIdentity &b);

/**
 * std::fstream subclass that handles UTF16 encoding on Windows.
 *
 * For documentation, see https://en.cppreference.com/w/cpp/io/basic_fstream
 */
class fstream : public std::fstream {
 public:
  fstream() = default;
  explicit fstream(const char *filepath,
                   std::ios_base::openmode mode = ios_base::in | ios_base::out);
  explicit fstream(const std::string &filepath,
                   std::ios_base::openmode mode = ios_base::in | ios_base::out);

  void open(StringRefNull filepath, ios_base::openmode mode = ios_base::in | ios_base::out);
};

}  // namespace blender
