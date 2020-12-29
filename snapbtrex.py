#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Author: Helge Jensen <hej@actua.dk>
# Author: Jonas von Malottki (yt) <yoshtec@gmx.net>
#
# Version history:
#
# 20150831 1.1 (yt)
# * made snapshots default to readonly
# * added EXEC as Keyword to find out on verbose what is actually executed
#
# 20160515 1.2 (yt)
# * remote linking to latest transferred snapshot
# * logging improvements
#
# 20160516 1.3 (yt)
# * remote deleting of snapshots
#
# 20160527 1.4 (yt)
# * Allowing just taking a snapshot without cleanup
#
# 20171202 1.5 (yt)
# * Local syncing of snapshots
# * Dry run mode
#
# 20171223 1.6 (yt)
# * Error handling
#
# 20180419 1.7 (yt)
# * Added --keep-only-latest modifier
# * --dry-run should not exit on deletion anymore
#
# 20191124 1.8 (yt)
# * fixed --sync-keep
#
#
# IDEA: change to different time format for integration with samba vfs
# https://www.samba.org/samba/docs/man/manpages/vfs_shadow_copy2.8.html

"""
snapbtrex is a small utility that keeps snapshots of btrfs filesystems
and optionally send it to a remote system.

snapbtrex is hosted on github:
https://github.com/yoshtec/snapbtrex

You can run it regularly (for example in a small script in
cron.hourly), or once in a while, to maintain an "interesting" (see
below) set of snapshots (backups). You may manually add or remove
snapshots as you like, use 'snapbtrex.DATE_FORMAT' (in GMT) as
snapshot-name.

It will keep at most --target-backups snapshots and ensure that
--target-freespace is available on the file-system by selecting
snapshots to remove.

Using --keep-backups, you can ensure that at least some backups are
kept, even if --target-freespace cannot be satisfied.

snapbtrex will keep backups with exponentially increasing distance as
you go back in time. It does this by selecting snapshots to remove as
follows.

The snapshots to remove is selected by "scoring" each space between
snapshots, (newer,older). snapbtrex will remove the older of the two
snapshots in the space that have the lowest score.

The scoring mechanism integrates e^x from (now-newer) to (now-older)
so, new pairs will have high value, even if they are tightly packed,
while older pairs will have high value if they are far apart.

The mechanism is completely self-contained and you can delete any
snapshot manually or any files in the snapshots.


== Transferring Snapshots to Remote Host

snapbtrex uses the btrfs send and receive commands to transfer
snapshots from a sending host to a receiving host.

Both hosts have to be prepared as in the setup instructions if
you want to call the script via cronjob.

== Setup instructions
transfer with backups with ssh

1. create user snapbtr on both systems
--
  sudo adduser snapbtr
--

2. generate ssh key on snd put public into rcv
--
  ssh-keygen
  ssh-copy-id snapbtr@123.45.56.78
--

3. create a sudoers file at the receiving machine
File: /etc/sudoers.d/90_snapbtrrcv

Precaution: depending on your distribution the path for btrfs tools might differ!

Minimum content is this for receiving snapshots on a remote system:
--
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs receive*
--

If you want to link the latest transferred item remotely to path then you'll
need another line (adopt path to your specific path):

--
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/ln -sfn /path/to/backups/* /path/to/current/current-link
--

If you need remote pruning then add this (you can also add the path for more secure setup):
--
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs subvolume delete*
--


4. Create a sudoers include file on the sending machine

File: /etc/sudoers.d/90_snapbtrsnd

Precaution: depending on your distribution the path for btrfs tools might differ!

Contents:
--
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs send*
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs filesystem sync*
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs subvolume*
--

"""

import itertools
import math
import os
import os.path
import sys
import time

DATE_FORMAT = "%Y%m%d-%H%M%S"  # date format used for directories to clean

DEFAULT_KEEP_BACKUPS = 10

LOG_LOCAL = "Local  > "
LOG_REMOTE = "Remote > "
LOG_EXEC = "EXEC  >-> "
LOG_STDERR = "STDERR > "
LOG_OUTPUT = "OUTPUT > "

# find TIME_SCALE: t < 2**32 => e**(t/c) < 2**32
TIME_SCALE = math.ceil(float((2 ** 32) / math.log(2 ** 32)))


def timef(x):
    # make value inverse exponential in the time passed
    try:
        v = math.exp(_timestamp(x) / TIME_SCALE)
    except ZeroDivisionError:
        v = None
    return v


def timestamp(x):
    try:
        v = _timestamp(x)
    except ValueError:
        v = None
    return v


def _timestamp(x):
    return time.mktime(time.strptime(os.path.split(x)[1], DATE_FORMAT))


def sorted_age(dirs, max_age):
    for xv, x in sorted((timestamp(y), y) for y in dirs):
        if xv < max_age:
            yield x


def first(it):
    for x in it:
        return x


def sorted_value(dirs):
    if len(dirs) <= 0:
        return dirs
    else:
        return _sorted_value(dirs)


def _sorted_value(dirs):
    # Iterate dirs, sorted by their relative value when deleted
    def poles(items):
        # Yield (items[0], items[1]), (items[1], items[2]), ... (items[n-1], items[n])
        rest = iter(items)
        last = next(rest)
        for n in rest:
            yield last, n
            last = n

    def all_but_last(items):
        # Yield items[0], ..., items[n-1]
        rest = iter(items)
        last = next(rest)
        for x in rest:
            yield last
            last = x

    # Remaining candidates for yield,
    # except the "max" one (latest)
    candidates = dict(
        all_but_last((x, xf) for xf, x in sorted((timef(y), y) for y in dirs) if xf)
    )
    # Keep going as long as there is anything to remove
    while len(candidates) > 1:
        # Get candidates ordered by timestamp (as v is monotonic in timestamp)
        remain = sorted((v, k) for k, v in candidates.items())
        # Find the "amount of information we loose by deleting the
        # latest of the pair"
        diffs = list(
            (to_tf - frm_tf, frm, to) for ((frm_tf, frm), (to_tf, to)) in poles(remain)
        )
        # Select the least important one
        mdiff, mfrm, mto = min(diffs)

        del candidates[mto]  # That's not a candidate any longer, it's gonna go
        yield mto

    # also, we must delete the last entry
    yield next(iter(candidates.keys()))


def freespace(path):
    st = os.statvfs(path)
    print(st)  # https://www.spinics.net/lists/linux-btrfs/msg103660.html
    return st.f_bavail * st.f_bsize


class Operations:
    def __init__(self, path, trace=None):
        self.tracef = trace
        self.path = path

    def check_call(self, args, shell=False, dry_safe=False):
        import subprocess

        cmd_str = " ".join(args)
        self.trace(LOG_EXEC + cmd_str)
        p = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=shell
        )
        stdout, stderr = p.communicate()
        if stdout:
            self.trace(LOG_OUTPUT + stdout.decode("utf-8"))
        if stderr:
            self.trace(LOG_STDERR + stderr.decode("utf-8"))
        if p.returncode != 0:
            raise RuntimeError(f"failed {cmd_str}")
        return stdout  # return the content

    def sync(self, dir):
        # syncing to be sure the operation is on the disc
        self.trace(LOG_LOCAL + f"sync filesystem {dir}")
        args = ["sudo", "btrfs", "filesystem", "sync", dir]
        self.check_call(args)
        self.trace(LOG_LOCAL + f"done sync filesystem {dir}")

    def unsnap(self, dir):
        self.unsnapx(os.path.join(self.path, dir))

    def unsnapx(self, dir):
        self.trace(LOG_LOCAL + f"remove snapshot {dir}")
        args = ["sudo", "btrfs", "subvolume", "delete", dir]
        self.check_call(args)
        self.trace(LOG_LOCAL + f"done remove snapshot {dir}")

    def freespace(self):
        return freespace(self.path)

    def listdir(self):
        return [d for d in os.listdir(self.path) if timef(d)]

    def listdir_path(self, target_path):
        return [d for d in os.listdir(target_path) if timef(d)]

    def listremote_dir(self, receiver, receiver_path, ssh_port):
        self.trace(
            LOG_REMOTE + f"list remote files host={receiver}, dir={receiver_path}"
        )
        args = ["ssh", "-p", ssh_port, receiver, "ls -1 " + receiver_path]
        return [
            d for d in self.check_call(args, dry_safe=True).splitlines() if timef(d)
        ]

    def snap(self, path):
        # yt: changed to readonly snapshots
        newdir = os.path.join(self.path, self.datestamp())
        self.trace(LOG_LOCAL + f"snapshotting path={path} to newdir={newdir}")
        args = ["sudo", "btrfs", "subvolume", "snapshot", "-r", path, newdir]
        self.check_call(args)
        self.sync(self.path)  # yt: make sure the new snap is on the disk
        self.trace(LOG_LOCAL + "done snapshotting")
        return newdir  # yt: return the latest snapshot

    @staticmethod
    def datestamp(secs=None):
        return time.strftime(DATE_FORMAT, time.gmtime(secs))

    def trace(self, *args, **kwargs):
        f = self.tracef
        if f:
            f(*args, **kwargs)

    def send_single(self, snap, receiver, receiver_path, ssh_port, rate_limit):
        self.trace(
            LOG_REMOTE
            + f"send single snapshot from {snap} to host {receiver} path={receiver_path}"
        )
        args = [
            f"sudo btrfs send -v {os.path.join(self.path, snap)}"
            + f" | pv -brtfL {rate_limit} | "
            + f"ssh -p {ssh_port} {receiver} 'sudo btrfs receive {receiver_path} '"
        ]
        # TODO: breakup the pipe stuff and do it without shell=True, currently it has problems with pipes :(
        self.check_call(args, shell=True)

    def send_withparent(
        self, parent_snap, snap, receiver, receiver_path, ssh_port, rate_limit
    ):
        self.trace(
            LOG_REMOTE
            + f"send snapshot from {snap} with parent {parent_snap} to host {receiver} path={receiver_path}"
        )
        args = [
            f"sudo btrfs send -v -p {os.path.join(self.path, parent_snap)} {os.path.join(self.path, snap)}"
            + f" | pv -brtfL {rate_limit} | "
            + f"ssh -p {ssh_port} {receiver} 'sudo btrfs receive -v {receiver_path} '"
        ]
        self.check_call(args, shell=True)
        self.trace(LOG_REMOTE + "finished sending snapshot")

    def link_current(self, receiver, receiver_path, snap, link_target, ssh_port):
        self.trace(
            LOG_REMOTE
            + f"linking current snapshot host={receiver} path={receiver_path} snap={snap} link={link_target}"
        )
        args = [
            "ssh",
            "-p",
            ssh_port,
            receiver,
            f"sudo ln -sfn '{os.path.join(receiver_path, snap)}' {link_target}",
        ]
        self.check_call(args)

    def remote_unsnap(self, receiver, receiver_path, dir, ssh_port):
        self.trace(
            LOG_REMOTE
            + f"delete snapshot {dir} from host={receiver} path={receiver_path}"
        )
        args = [
            "ssh",
            "-p",
            ssh_port,
            receiver,
            f"sudo btrfs subvolume delete '{os.path.join(receiver_path, dir)}'",
        ]
        self.check_call(args)
        self.trace(LOG_REMOTE + "deleted")

    def sync_single(self, snap, target):
        self.trace(LOG_LOCAL + "sync single snapshot %s to %s", snap, target)
        args = [
            f"sudo btrfs send -v {os.path.join(self.path, snap)}"
            + " | pv -brtf | "
            + f"sudo btrfs receive -v {target}"
        ]
        self.check_call(args, shell=True)

    def sync_withparent(self, parent_snap, snap, target_path):
        self.trace(
            LOG_LOCAL
            + f"send snapshot from {snap} with parent {parent_snap} to path={target_path}"
        )
        args = [
            f"sudo btrfs send -v -p {os.path.join(self.path, parent_snap)} {os.path.join(self.path, snap)}"
            " | pv -brtf | "
            f"sudo btrfs receive -v {target_path}"
        ]
        self.check_call(args, shell=True)


# Allows to Simulate operations
class DryOperations(Operations):
    def __init__(self, path, trace=None):
        Operations.__init__(self, path=path, trace=trace)
        self.dirs = None

    def check_call(self, args, shell=False, dry_safe=False):
        cmd_str = " ".join(args)
        if dry_safe:
            self.trace(LOG_EXEC + "executing dry-safe command: " + cmd_str)
            return Operations.check_call(self, args, shell, dry_safe)
        else:
            self.trace(LOG_EXEC + cmd_str)

    # added to simulate also the deletion of snapshots
    def listdir(self):
        if self.dirs is None:
            self.dirs = [d for d in os.listdir(self.path) if timef(d)]
        return self.dirs

    def unsnap(self, dir):
        Operations.unsnap(self, dir)
        self.dirs.remove(dir)


class FakeOperations(DryOperations):
    def __init__(self, path, trace=None, dirs=None, space=None, snap_space=None):
        Operations.__init__(self, path=path, trace=trace)
        if dirs is None:
            dirs = {}
        if space is None:
            space = 0
        self.dirs = dirs
        self.space = space
        if snap_space is None:
            snap_space = 1
        self.snap_space = snap_space

    def snap(self, path):
        self.dirs[self.datestamp()] = self.snap_space
        Operations.snap(self, path)

    def unsnap(self, dir):
        v = self.dirs[dir]
        self.space += v
        Operations.unsnap(self, dir)
        del self.dirs[dir]

    def listdir(self):
        self.trace(f"listdir() = {self.dirs.keys()}")
        return self.dirs.keys()

    def listdir_path(self, target_path):
        dirs = ["20101201-030000", "20101201-040000", "20101201-050000"]
        self.trace(f"listdir_path() values={dirs}")
        return dirs

    def listremote_dir(self, receiver, receiver_path, ssh_port):
        dirs = [
            "20101201-030000",
            "20101201-040000",
            "20101201-050000",
            "20101201-070000",
        ]
        self.trace(f"listremotedir() r={receiver}, rp={receiver_path}, values={dirs}")
        return dirs

    def freespace(self):
        self.trace(f"freespace() = {self.space}")
        return self.space


def cleandir(operations, targets):
    """ Perform actual cleanup of using 'operations' until 'targets' are met """

    trace = operations.trace
    keep_backups = targets.keep_backups
    keep_latest = targets.keep_latest
    target_fsp = targets.target_freespace
    target_backups = targets.target_backups
    max_age = targets.max_age
    was_above_target_freespace = None
    was_above_target_backups = None
    last_dirs = []

    trace(
        LOG_LOCAL
        + f"Parameters for cleandir: keep_backups={keep_backups}, target_freespace={target_fsp}, "
        f"target_backups={target_backups}, max_age={max_age}, keep_latest={keep_latest}"
    )
    next_del = None

    while True:
        do_del = None
        dirs = sorted(operations.listdir())
        dirs_len = len(dirs)
        if dirs_len <= 0:
            raise Exception("No more directories to clean")
        elif dirs == last_dirs:
            raise Exception(f"Could not delete last snapshot: {next_del}")
        else:
            last_dirs = dirs

        # check at least keep this amount of backups
        if keep_backups is not None:
            if dirs_len <= keep_backups:
                trace(
                    LOG_LOCAL
                    + f"Reached number of minimum backups to keep: {dirs_len}, stopping further deletion"
                )
                break

        if target_fsp is not None:
            fsp = operations.freespace()
            # print "+++ ", fsp, target_fsp, fsp >= target_fsp
            if fsp >= target_fsp:
                if was_above_target_freespace or was_above_target_freespace is None:
                    trace(
                        LOG_LOCAL
                        + f"Satisfied freespace target: {fsp} with {target_fsp}"
                    )
                    was_above_target_freespace = False
                if do_del is None:
                    do_del = False
            else:
                if was_above_target_freespace is None:
                    was_above_target_freespace = True
                do_del = True

        if target_backups is not None:
            if dirs_len <= target_backups:
                if was_above_target_backups or was_above_target_backups is None:
                    trace(
                        LOG_LOCAL
                        + f"Satisfied target number of backups: {target_backups} with {dirs_len}"
                    )
                    was_above_target_backups = False
                if do_del is None:
                    do_del = False
            else:
                if was_above_target_backups is None:
                    was_above_target_backups = True
                do_del = True

        if not do_del:
            break

        next_del = None
        if max_age is not None:
            next_del = first(sorted_age(dirs, max_age))
        # remove latest first only if the keep_latest is 'True'
        if keep_latest is not None and keep_latest:
            next_del = first(dirs)
        if next_del is None:
            next_del = first(sorted_value(dirs))
        else:
            trace(LOG_LOCAL + "will delete backup: '%s'", operations.datestamp(max_age))
        if next_del is None:
            trace(LOG_LOCAL + "No more backups left")
            break
        else:
            operations.unsnap(next_del)


def transfer(operations, target_host, target_dir, link_dir, ssh_port, rate_limit):
    """ Transfer snapshots to remote host """

    trace = operations.trace

    # find out what kind of snapshots exist on the remote host
    targetsnaps = set(operations.listremote_dir(target_host, target_dir, ssh_port))
    localsnaps = set(operations.listdir())

    if len(localsnaps) == 0:
        # nothing to do here, no snaps here
        return

    parents = targetsnaps.intersection(localsnaps)

    # no parent exists so
    if len(parents) == 0:
        # start transferring the oldest snapshot
        # by that snapbtrex will transfer all snapshots that have been created
        operations.send_single(
            min(localsnaps), target_host, target_dir, ssh_port, rate_limit
        )
        parents.add(min(localsnaps))

    # parent existing, use the latest as parent
    max_parent = max(parents)
    parent = max_parent

    trace(LOG_REMOTE + f"last possible parent = {max_parent}")

    for s in sorted(localsnaps):
        if s > max_parent:
            trace(LOG_REMOTE + f"transfer: parent={parent} snap={s}")
            operations.send_withparent(
                parent, s, target_host, target_dir, ssh_port, rate_limit
            )
            if link_dir is not None:
                operations.link_current(target_host, target_dir, s, link_dir, ssh_port)
            # advance one step
            parent = s


def remotecleandir(operations, target_host, target_dir, remote_keep, ssh_port):
    """ Perform remote cleanup using 'operations' until exactly remote_keep backups are left """
    trace = operations.trace

    if remote_keep is not None:
        dirs = sorted(
            operations.listremote_dir(
                receiver=target_host, receiver_path=target_dir, ssh_port=ssh_port
            )
        )
        dirs_len = len(dirs)
        if dirs_len <= remote_keep or remote_keep <= 0:
            trace(
                LOG_REMOTE
                + "No remote directories to clean, currently %s remote backups, should keep %s",
                dirs_len,
                remote_keep,
            )
        else:
            delete_dirs = sorted_value(dirs)
            del_count = dirs_len - remote_keep
            trace(
                LOG_REMOTE
                + f"about to remove {del_count} of out of {dirs_len} backups, keeping {remote_keep}"
            )
            for del_dir in itertools.islice(delete_dirs, del_count):
                if del_dir is None:
                    trace(LOG_REMOTE + "No more backups left")
                    break
                else:
                    operations.remote_unsnap(target_host, target_dir, del_dir, ssh_port)


def sync_local(operations, sync_dir):
    """ Transfer snapshots to local target """
    trace = operations.trace

    # find out what kind of snapshots exist on the remote host
    targetsnaps = set(operations.listdir_path(sync_dir))
    localsnaps = set(operations.listdir())

    if len(localsnaps) == 0:
        # nothing to do here, no snaps here
        return

    parents = targetsnaps.intersection(localsnaps)

    # no parent exists so
    if len(parents) == 0:
        # start transferring the oldest snapshot
        # by that snapbtrex will transfer all snapshots that have been created
        operations.sync_single(min(localsnaps), sync_dir)
        parents.add(min(localsnaps))

    # parent existing, use the latest as parent
    max_parent = max(parents)
    parent = max_parent

    trace(LOG_LOCAL + f"Sync: last possible parent = {max_parent}")

    for s in sorted(localsnaps):
        if s > max_parent:
            trace(LOG_LOCAL + f"transfer: parent={parent} snap={s}")
            operations.sync_withparent(parent, s, sync_dir)
            # if link_dir is not None:
            #    operations.link_current(target_host, target_dir, s, link_dir, ssh_port)
            parent = s


def sync_cleandir(operations, target_dir, sync_keep):
    """ Perform local sync cleanup using 'operations' until exactly sync_keep backups are left """
    trace = operations.trace

    if sync_keep is not None:
        dirs = sorted(operations.listdir_path(target_dir))
        dirs_len = len(dirs)
        if dirs_len <= sync_keep or sync_keep <= 0:
            trace(
                LOG_LOCAL
                + f"No synced directories to clean, currently {dirs_len} synced backups, should keep {sync_keep}"
            )
        else:
            delete_dirs = sorted_value(dirs)
            del_count = dirs_len - sync_keep
            trace(
                LOG_LOCAL
                + f"about to remove sync {del_count} of out of {dirs_len} synced backups, keeping {sync_keep}"
            )
            for del_dir in itertools.islice(delete_dirs, del_count):
                trace(LOG_LOCAL + "removing: ")
                if del_dir is None:
                    trace(LOG_LOCAL + "No more synced backups left")
                    break
                else:
                    operations.unsnapx(os.path.join(target_dir, del_dir))


def log_trace(fmt, *args, **kwargs):
    tt = time.strftime(DATE_FORMAT, time.gmtime(None)) + ": "
    if args is not None:
        print(tt + (fmt % args))
    elif kwargs is not None:
        print(tt + (fmt % kwargs))
    else:
        print(tt + fmt)


def default_trace(fmt, *args, **kwargs):
    if args is not None:
        print(fmt % args)
    elif kwargs is not None:
        print(fmt % kwargs)
    else:
        print(fmt)


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

    if pa.snap:
        operations.snap(path=pa.snap)

    # remote transfer: host and remote dir are needed
    if not (pa.remote_host is None and pa.remote_dir is None):
        try:
            transfer(
                operations,
                pa.remote_host,
                pa.remote_dir,
                pa.remote_link,
                pa.ssh_port,
                pa.rate_limit,
            )
            if pa.remote_keep is not None:
                remotecleandir(
                    operations,
                    pa.remote_host,
                    pa.remote_dir,
                    pa.remote_keep,
                    pa.ssh_port,
                )
        except RuntimeError as e:
            trace(LOG_REMOTE + f"Error while transferring to remote host: {e}")

    # Local sync to another path
    if pa.sync_dir is not None:
        try:
            sync_local(operations, pa.sync_dir)
            if pa.sync_keep is not None:
                sync_cleandir(operations, pa.sync_dir, pa.sync_keep)
        except RuntimeError as e:
            trace(LOG_LOCAL + f"ERROR while Syncing local: {e}")

    # cleanup local
    if pa.target_freespace is not None or pa.target_backups is not None:
        try:
            if pa.keep_backups == DEFAULT_KEEP_BACKUPS:
                trace(
                    LOG_LOCAL
                    + f"using default value for --keep-backups: {DEFAULT_KEEP_BACKUPS}"
                )
            cleandir(operations=operations, targets=pa)
        except RuntimeError as e:
            trace(LOG_LOCAL + f"ERROR while cleaning up: {e}")
    else:
        trace(
            LOG_LOCAL + "no options for cleaning were passed -> keeping all snapshots"
        )


if "__main__" == __name__:
    sys.exit(main(sys.argv))
