import sys, time
from .operations import (
    FakeOperations,
    Operations,
    DryOperations,
    DATE_FORMAT,
    DEFAULT_KEEP_BACKUPS,
)


def default_trace(fmt, *args, **kwargs):
    try:
        if args:
            print(fmt % args)
        elif kwargs:
            print(fmt % kwargs)
        else:
            print(fmt)
    except (Exception,):
        print(fmt)


def log_trace(fmt, *args, **kwargs):
    tt = time.strftime(DATE_FORMAT, time.gmtime(None)) + ": " + fmt
    default_trace(tt, *args, **kwargs)


def null_trace(fmt, *args, **kwargs):
    pass


def main(argv):
    import argparse

    class UnitInt(int):
        format = ""
        mods = {}

        @staticmethod
        def parse(cls, target_str):
            import re

            form = cls.format % "|".join(x for x in cls.mods.keys() if x is not None)
            m = re.match(form, target_str, re.IGNORECASE)
            if m:
                val, mod = m.groups()
                result = cls.eval(int(val), mod)
                return result
            else:
                raise ValueError(f"Invalid value: {target_str}, expected: {form}")

        def __init__(self, value):
            super().__init__(value)
            self.origin = value

        def __new__(cls, value=0):
            if isinstance(value, str):
                value = UnitInt.parse(cls, value)
            return value

        def __str__(self):
            if isinstance(self.origin, int):
                return str(self.origin)
            else:
                return "%s[%s]" % (self.origin, int(self))

    class Space(UnitInt):
        format = "([0-9]+)(%s)?"
        mods = {None: 0, "K": 1, "M": 2, "G": 3, "T": 4}

        @staticmethod
        def eval(val, mod):
            if mod is None:
                return val
            else:
                return val * 1024 ** Space.mods[mod.upper()]

    class Age(UnitInt):
        format = "([0-9]+)(%s)?"
        mods = {
            None: 1,
            "s": 1,
            "m": 60,
            "h": 60 * 60,
            "d": 24 * 60 * 60,
            "w": 7 * 24 * 60 * 60,
            "y": (52 * 7 + 1) * 24 * 60 * 60,  # year = 52 weeks + 1 or 2 days
        }

        @staticmethod
        def eval(val, mod):
            if mod is None:
                return max(0, time.time() - val)
            else:
                return max(0, time.time() - val * Age.mods[mod.lower()])

    def parse_ageoffset_to_timestamp(age_str):
        now = time.time()
        age = int(age_str)
        if age > now:
            raise "Invalid value: %d, expected less than: %d" % (age, now)
        else:
            return float(now - age)

    parser = argparse.ArgumentParser(
        description="Keep btrfs snapshots for backup, optionally sync to snapshots locally or sends snapshots to "
        "remote systems via ssh. Visit https://github.com/yoshtec/snapbtrex for more insight."
    )

    parser.add_argument(
        "--path",
        "-p",
        "--snap-to",
        metavar="PATH",
        required=False,
        help="Target path for new snapshots and cleanup operations",
    )

    target_group = parser.add_argument_group(
        title="Cleanup", description="Delete backup snapshots until the targets are met"
    )

    target_group.add_argument(
        "--target-freespace",
        "-F",
        dest="target_freespace",
        metavar="SIZE",
        default=None,
        type=Space,
        help="Cleanup PATH until at least SIZE is free. SIZE is #bytes, "
        + "or given with K, M, G or T respectively for kilo, ...",
    )

    target_group.add_argument(
        "--target-backups",
        "-B",
        dest="target_backups",
        metavar="#",
        type=int,
        help="Cleanup PATH until at most B backups remain",
    )

    target_group.add_argument(
        "--keep-backups",
        "-K",
        metavar="N",
        type=int,
        default=DEFAULT_KEEP_BACKUPS,
        help="Keep minimum of N backups -> This is a lower bound. the lower bound is valid for all other options",
    )

    target_group.add_argument(
        "--max-age",
        "-A",
        dest="max_age",
        metavar="MAX_AGE",
        default=None,
        type=Age,
        help="Prefer removal of backups older than MAX_AGE seconds. MAX_AGE is #seconds, "
        + "or given with m (minutes), h (hours), d (days), w (weeks), y (years = 52w + 1d).",
    )

    target_group.add_argument(
        "--keep-only-latest",
        "-L",
        dest="keep_latest",
        action="store_true",
        help="lets you keep only the latest snapshots",
    )

    snap_group = parser.add_mutually_exclusive_group(required=False)

    snap_group.add_argument(
        "--snap",
        "-s",
        "--snap-this",
        metavar="SUBVOL",
        default=".",
        help="Take snapshot of SUBVOL on invocation",
    )

    snap_group.add_argument(
        "--no-snap",
        "-S",
        dest="snap",
        help="Do not take snapshot",
        action="store_const",
        const=None,
    )

    parser.add_argument("--test", help="Execute built-in tests", action="store_true")

    parser.add_argument(
        "--explain", help="Explain what %(prog)s does (and stop)", action="store_true"
    )

    parser.add_argument(
        "--dry-run",
        help="Do not execute commands, but print shell commands to stdout that would be executed",
        dest="dry_run",
        action="store_true",
    )

    parser.add_argument("--verbose", "-v", help="Verbose output", action="store_true")

    transfer_group = parser.add_argument_group(
        title="Transfer",
        description="Transfer snapshots to other hosts via ssh. "
        + "It is assumed that the user running the script is run can connect to the remote host "
        + "via keys and without passwords. See --explain or visit the homepage for more info",
    )

    transfer_group.add_argument(
        "--remote-host",
        metavar="HOST",
        dest="remote_host",
        help="Transfer to target host via ssh.",
    )

    transfer_group.add_argument(
        "--remote-dir",
        metavar="PATH",
        dest="remote_dir",
        help="Transfer the snapshot to this PATH on the target host",
    )

    transfer_group.add_argument(
        "--remote-link",
        metavar="LINK",
        dest="remote_link",
        help="Create a link the transferred snapshot to this LINK",
    )

    transfer_group.add_argument(
        "--remote-keep",
        metavar="N",
        type=int,
        dest="remote_keep",
        help="Cleanup remote backups until N backups remain, if unset keep all remote transferred backups",
    )

    transfer_group.add_argument(
        "--ssh-port", metavar="SSHPORT", dest="ssh_port", default="22", help="SSH port"
    )

    transfer_group.add_argument(
        "--rate-limit",
        metavar="RATE",
        dest="rate_limit",
        default="0",
        help="Limit the transfer to a maximum of RATE bytes per "
        + 'second. A suffix of "k", "m", "g", or "t" can be added '
        + "to denote kilobytes (*1024), megabytes, and so on.",
    )

    sync_group = parser.add_argument_group(
        title="Sync Local",
        description="Transfer snapshots to another local (btrfs) filesystem.",
    )

    sync_group.add_argument(
        "--sync-target",
        metavar="PATH",
        dest="sync_dir",
        help="Copy snapshot to this path",
    )

    sync_group.add_argument(
        "--sync-keep",
        metavar="N",
        type=int,
        dest="sync_keep",
        help="Cleanup local synced backups until N backups remain, if unset keep all locally synced backups",
    )

    # safety net if no arguments are given call for usage
    if len(sys.argv[1:]) == 0:
        parser.print_usage()
        return 0

    pa = parser.parse_args()

    if pa.verbose:
        if sys.stdout.isatty():
            trace = default_trace
        else:
            # use logging with timestamps on script output
            trace = log_trace
    else:
        trace = null_trace

    if pa.explain:
        sys.stdout.write(__doc__)
        return 0

    if pa.path is None:
        print("Path is missing")
        parser.print_help()
        return 1

    # test if pv is installed for needed actions
    if (
        not (pa.remote_host is None and pa.remote_dir is None)
        or pa.sync_dir is not None
    ):
        import shutil

        pv = shutil.which("pv")
        if pv is None:
            print("Error: Missing dependency 'pv' for transfer of snapshots")
            print("install e.g. via 'apt install pv'")
            return 1

    if pa.test:
        trace("## TEST ##")
        trace(
            "## TEST ## Testing mode: all operations are only displayed without execution"
        )
        trace("## TEST ##")
        operations = FakeOperations(
            path=pa.path,
            trace=trace,
            dirs={
                "20101201-000000": 0,
                "20101201-010000": 1,
                "20101201-020000": 2,
                "20101201-030000": 3,
                "20101201-040000": 4,
                "20101201-050000": 5,
                "20101201-060000": 6,
                "20101201-070000": 7,
                "20101201-080000": 8,
            },
            space=5,
        )
    elif pa.dry_run:
        trace("## DRY RUN ##")
        trace(
            "## DRY RUN ## Dry Run mode: disk-modifying operations are only displayed without execution"
        )
        trace("## DRY RUN ##")
        operations = DryOperations(path=pa.path, trace=trace)
    else:
        operations = Operations(path=pa.path, trace=trace)

    # -- Actions --
    # 1. Snapshot
    if pa.snap:
        operations.snap(path=pa.snap)

    # 2. remote transfer: host and remote dir are needed
    if not (pa.remote_host is None and pa.remote_dir is None):
        try:
            operations.transfer(
                pa.remote_host,
                pa.remote_dir,
                pa.remote_link,
                pa.ssh_port,
                pa.rate_limit,
            )
            if pa.remote_keep is not None:
                operations.remotecleandir(
                    pa.remote_host,
                    pa.remote_dir,
                    pa.remote_keep,
                    pa.ssh_port,
                )
        except RuntimeError as e:
            operations.log_remote(f"Error while transferring to remote host: {e}")

    # 3. Local sync to another path
    if pa.sync_dir is not None:
        try:
            operations.sync_local(pa.sync_dir)
            if pa.sync_keep is not None:
                operations.sync_cleandir(pa.sync_dir, pa.sync_keep)
        except RuntimeError as e:
            operations.log_local(f"ERROR while Syncing local: {e}")

    # 4. Cleanup local
    if pa.target_freespace is not None or pa.target_backups is not None:
        try:
            if pa.keep_backups == DEFAULT_KEEP_BACKUPS:
                operations.log_local(
                    f"using default value for --keep-backups: {DEFAULT_KEEP_BACKUPS}"
                )
            operations.cleandir(pa)
        except RuntimeError as e:
            operations.log_local(f"ERROR while cleaning up: {e}")
    else:
        operations.log_local(
            "no options for cleaning were passed -> keeping all snapshots"
        )
