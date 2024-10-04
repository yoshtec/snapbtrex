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

snapbtrex is hosted on GitHub:
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

The mechanism is completely self-contained, and you can delete any
snapshot manually.


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
TIME_SCALE = math.ceil(float((2**32) / math.log(2**32)))


def timef(x):
    # make value inverse exponential in the time passed
    try:
        v = math.exp(timestamp(x) / TIME_SCALE)
    except ZeroDivisionError:
        v = None
    return v


def timestamp(x):
    try:
        v = time.mktime(time.strptime(os.path.split(x)[1], DATE_FORMAT))
    except ValueError:
        v = None
    return v


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

        del candidates[mto]  # That's not a candidate any longer, it's got to go
        yield mto

    # also, we must delete the last entry
    yield next(iter(candidates.keys()))


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

        if stderr:
            stderr = stderr.decode(encoding=sys.stderr.encoding, errors="ignore")
            self.trace(LOG_STDERR + stderr)

        if stdout:
            stdout = stdout.decode(encoding=sys.stdout.encoding, errors="ignore")
            self.trace(LOG_OUTPUT + stdout)

        if p.returncode != 0:
            raise RuntimeError(f"failed {cmd_str}")
        return stdout  # return the content

    def sync(self, dir):
        # syncing to be sure the operation is on the disc
        self.trace(f"{LOG_LOCAL} sync filesystem {dir}")
        args = ["sudo", "btrfs", "filesystem", "sync", dir]
        self.check_call(args)
        self.trace(f"{LOG_LOCAL} done sync filesystem {dir}")

    def unsnap(self, dir):
        self.unsnapx(os.path.join(self.path, dir))

    def unsnapx(self, dir):
        self.trace(f"{LOG_LOCAL} remove snapshot {dir}")
        args = ["sudo", "btrfs", "subvolume", "delete", dir]
        self.check_call(args)
        self.trace(f"{LOG_LOCAL} done remove snapshot {dir}")

    def freespace(self):
        # sync filesystem before assessing the free space
        self.sync(self.path)
        st = os.statvfs(self.path)
        self.trace(f"{LOG_LOCAL} filesystem info: {st}")
        # https://www.spinics.net/lists/linux-btrfs/msg103660.html
        return st.f_bavail * st.f_bsize

    def listdir(self):
        return [d for d in os.listdir(self.path) if timef(d)]

    def listdir_path(self, target_path):
        return [d for d in os.listdir(target_path) if timef(d)]

    def listremote_dir(self, receiver, receiver_path, ssh_port):
        self.trace(
            f"{LOG_REMOTE} list remote files host={receiver}, dir={receiver_path}"
        )
        args = ["ssh", "-p", ssh_port, receiver, f"ls -1 {receiver_path}"]
        return [
            d for d in self.check_call(args, dry_safe=True).splitlines() if timef(d)
        ]

    def snap(self, path):
        # yt: changed to readonly snapshots
        newdir = os.path.join(self.path, self.datestamp())
        self.trace(f"{LOG_LOCAL} snapshotting path={path} to newdir={newdir}")
        args = ["sudo", "btrfs", "subvolume", "snapshot", "-r", path, newdir]
        self.check_call(args)
        self.sync(self.path)  # yt: make sure the new snap is on the disk
        self.trace(LOG_LOCAL + "done snapshotting")
        return newdir  # yt: return the latest snapshot

    @staticmethod
    def datestamp(secs=None):
        return time.strftime(DATE_FORMAT, time.gmtime(secs))

    def trace(self, *args, **kwargs):
        if self.tracef:
            self.tracef(*args, **kwargs)

    def log_local(self, message: str):
        self.trace(LOG_LOCAL + message)

    def log_remote(self, message: str):
        self.trace(LOG_REMOTE + message)

    def send_single(self, snap, receiver, receiver_path, ssh_port, rate_limit):
        self.log_remote(
            f"send single snapshot={snap} from path={self.path} to host={receiver} path={receiver_path}"
        )
        args = [
            f"sudo btrfs send {os.path.join(self.path, snap)}"
            f" | pv -brtfL {rate_limit} | "
            f"ssh -p {ssh_port} {receiver} 'sudo btrfs receive {receiver_path} '"
        ]
        # TODO: breakup the pipe stuff and do it without shell=True, currently it has problems with pipes :(
        self.check_call(args, shell=True)

    def send_withparent(
        self, parent_snap, snap, receiver, receiver_path, ssh_port, rate_limit
    ):
        self.log_remote(
            f"send snapshot={snap} from path={self.path} with parent={parent_snap}"
            f"to host={receiver} path={receiver_path}"
        )
        args = [
            f"sudo btrfs send -p {os.path.join(self.path, parent_snap)} {os.path.join(self.path, snap)}"
            f" | pv -brtfL {rate_limit} | "
            f"ssh -p {ssh_port} {receiver} 'sudo btrfs receive {receiver_path} '"
        ]
        self.check_call(args, shell=True)
        self.log_remote("finished sending snapshot")

    def link_current(self, receiver, receiver_path, snap, link_target, ssh_port):
        self.log_remote(
            f"linking current snapshot host={receiver} path={receiver_path} snap={snap} link={link_target}"
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
        self.log_remote(
            f"delete snapshot {dir} from host={receiver} path={receiver_path}"
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
        self.log_local(f"sync single snapshot={snap} to={target}")
        args = [
            f"sudo btrfs send {os.path.join(self.path, snap)}"
            f" | pv -brtf | "
            f"sudo btrfs receive {target}"
        ]
        self.check_call(args, shell=True)

    def sync_withparent(self, parent_snap, snap, target_path):
        self.log_local(
            f"send snapshot={snap} from={self.path} with parent={parent_snap} to path={target_path}"
        )
        args = [
            f"sudo btrfs send -v -p {os.path.join(self.path, parent_snap)} {os.path.join(self.path, snap)}"
            " | pv -brtf | "
            f"sudo btrfs receive -v {target_path}"
        ]
        self.check_call(args, shell=True)

    def cleandir(self, targets):
        """Perform actual cleanup of using 'operations' until 'targets' are met"""

        keep_backups = targets.keep_backups
        keep_latest = targets.keep_latest
        target_fsp = targets.target_freespace
        target_backups = targets.target_backups
        max_age = targets.max_age
        was_above_target_freespace = None
        was_above_target_backups = None
        last_dirs = []

        self.log_local(
            f"Parameters for clean dir: keep_backups={keep_backups}, target_freespace={target_fsp}, "
            f"target_backups={target_backups}, max_age={max_age}, keep_latest={keep_latest}"
        )
        next_del = None

        while True:
            do_del = None
            dirs = sorted(self.listdir())
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
                    self.log_local(
                        f"current amount of backups: {dirs_len} have to keep a minimum of {keep_backups}, "
                        f"stopping further deletion"
                    )
                    break

            if target_fsp is not None:
                fsp = self.freespace()
                # print "+++ ", fsp, target_fsp, fsp >= target_fsp
                if fsp >= target_fsp:
                    if was_above_target_freespace or was_above_target_freespace is None:
                        self.log_local(
                            f"Satisfied freespace target={target_fsp}; current free space={fsp}"
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
                        self.log_local(
                            f"Satisfied target number of backups: {target_backups} with {dirs_len}"
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
            # remove the latest first only if the keep_latest is 'True'
            if keep_latest is not None and keep_latest:
                next_del = first(dirs)
            if next_del is None:
                next_del = first(sorted_value(dirs))
            else:
                self.log_local(f"will delete backup: '{self.datestamp(max_age)}'")
            if next_del is None:
                self.log_local("No more backups left")
                break
            else:
                self.unsnap(next_del)

    def transfer(self, target_host, target_dir, link_dir, ssh_port, rate_limit):
        """Transfer snapshots to remote host"""

        # find out what kind of snapshots exist on the remote host
        targetsnaps = set(self.listremote_dir(target_host, target_dir, ssh_port))
        localsnaps = set(self.listdir())

        if len(localsnaps) == 0:
            # nothing to do here, no snaps here
            return

        parents = targetsnaps.intersection(localsnaps)

        # no parent exists so
        if len(parents) == 0:
            # start transferring the oldest snapshot
            # by that snapbtrex will transfer all snapshots that have been created
            self.send_single(
                min(localsnaps), target_host, target_dir, ssh_port, rate_limit
            )
            parents.add(min(localsnaps))

        # parent existing, use the latest as parent
        max_parent = max(parents)
        parent = max_parent

        self.log_remote(f"last possible parent = {max_parent}")

        for s in sorted(localsnaps):
            if s > max_parent:
                self.log_remote(f"transfer: parent={parent} snap={s}")
                self.send_withparent(
                    parent, s, target_host, target_dir, ssh_port, rate_limit
                )
                if link_dir is not None:
                    self.link_current(target_host, target_dir, s, link_dir, ssh_port)
                # advance one step
                parent = s

    def remotecleandir(self, target_host, target_dir, remote_keep, ssh_port):
        """Perform remote cleanup using 'operations' until exactly remote_keep backups are left"""

        if remote_keep is not None:
            dirs = sorted(
                self.listremote_dir(
                    receiver=target_host, receiver_path=target_dir, ssh_port=ssh_port
                )
            )
            dirs_len = len(dirs)
            if dirs_len <= remote_keep or remote_keep <= 0:
                self.log_remote(
                    "No remote directories to clean, currently %s remote backups, should keep %s".format(
                        dirs_len, remote_keep
                    )
                )
            else:
                delete_dirs = sorted_value(dirs)
                del_count = dirs_len - remote_keep
                self.log_remote(
                    f"about to remove {del_count} of out of {dirs_len} backups, keeping {remote_keep}"
                )
                for del_dir in itertools.islice(delete_dirs, del_count):
                    if del_dir is None:
                        self.log_remote("No more backups left")
                        break
                    else:
                        self.remote_unsnap(target_host, target_dir, del_dir, ssh_port)

    def sync_local(self, sync_dir):
        """Transfer snapshots to local target"""

        # find out what kind of snapshots exist on the remote host
        targetsnaps = set(self.listdir_path(sync_dir))
        localsnaps = set(self.listdir())

        if len(localsnaps) == 0:
            # nothing to do here, no snaps here
            return

        parents = targetsnaps.intersection(localsnaps)

        # no parent exists so
        if len(parents) == 0:
            # start transferring the oldest snapshot
            # by that snapbtrex will transfer all snapshots that have been created
            self.sync_single(min(localsnaps), sync_dir)
            parents.add(min(localsnaps))

        # parent existing, use the latest as parent
        max_parent = max(parents)
        parent = max_parent

        self.log_local(f"Sync: last possible parent = {max_parent}")

        for s in sorted(localsnaps):
            if s > max_parent:
                self.log_local(f"transfer: parent={parent} snap={s}")
                self.sync_withparent(parent, s, sync_dir)
                # if link_dir is not None:
                #    operations.link_current(target_host, target_dir, s, link_dir, ssh_port)
                parent = s

    def sync_cleandir(self, target_dir, sync_keep):
        """Perform local sync cleanup using 'operations' until exactly sync_keep backups are left"""

        if sync_keep is not None:
            dirs = sorted(self.listdir_path(target_dir))
            dirs_len = len(dirs)
            if dirs_len <= sync_keep or sync_keep <= 0:
                self.log_local(
                    f"No synced directories to clean, currently {dirs_len} synced backups, should keep {sync_keep}"
                )
            else:
                delete_dirs = sorted_value(dirs)
                del_count = dirs_len - sync_keep
                self.log_local(
                    f"about to remove sync {del_count} of out of {dirs_len} synced backups, keeping {sync_keep}"
                )
                for del_dir in itertools.islice(delete_dirs, del_count):
                    self.log_local("removing: ")
                    if del_dir is None:
                        self.log_local("No more synced backups left")
                        break
                    else:
                        self.unsnapx(os.path.join(target_dir, del_dir))


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
