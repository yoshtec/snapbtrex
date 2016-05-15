#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Autor: Helge Jensen <hej@actua.dk>
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
# TODO: remote pruning
# TODO: remove shell = True for ssh stuff
# TODO: change to different time format for integration with smaba vfs https://www.samba.org/samba/docs/man/manpages/vfs_shadow_copy2.8.html


"""
snapbtrex is a small utility that keeps snapshots of btrfs filesystems
and optionally send it to a remote system.

You can run it regularly (for example in a small script in
cron.hourly), or once in a while, to maintain an "interesting" (see
below) set of snapshots (backups). You may manually add or remove
snapshots as you like, use 'snapbtr.DATE_FORMAT' (in GMT) as
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
snapshots, (newer,older). snapbtr will remove the older of the two
snapshots in the space that have the lowest score.

The scoring mechanism integrates e^x from (now-newer) to (now-older)
so, new pairs will have high value, even if they are tightly packed,
while older pairs will have high value if they are far apart.

The mechanism is completely self-contained and you can delete any
snapshot manually or any files in the snapshots.



== Transferring Snapshots to Remote Host

snapbtrex uses the btrfs send and recieve commands to transfer
snapshots from a sendin host to a receiving host.

Both hosts have to be prepared as in the setup instructions if
you want to call the script via cronjob.

== Setup instructions
transfer with backups with ssh

1. create user snapbtr on both systems
  sudo adduser snapbtr


2. generate ssh key on snd put public into rcv

  ssh-keygen -t rsa

  ssh-copy-id snapbtr@123.45.56.78


3. create a sudoers file at the receiving machine
File: /etc/sudoers.d/90_snapbtrrcv

Precaution: depending on your distrubution the path for btrfs tools might differ!

Minumum content is this for recieving snapshots on a remote system:
--
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs receive*
--

If you want to link the latest transferred item remotely to path then you'll
need another line (adopt path to your specific path):

--
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/ln -sfn /path/to/backups/* /path/to/current/current-link
--

If you need remote pruning then add this:
--
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs receive*
--


4. Create a sudoers include file on the sending machine

File: /etc/sudoers.d/90_snapbtrsnd

Precaution: depending on your distrubution the path for btrfs tools might differ!

Contents:
--
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs send*
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs snapshot*
  snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs filesystem sync*
--

== Precautions
if you created your snapshots with an older version of snapbtr than those
snapshots had been created as rewad/write snapshots. The sending of snapshots
to remote hosts demands that those snaps are readonly. You can change rw snaps
to ro snaps in the directory of the snapshots via:

  sudo find . -maxdepth 1 -type d -exec btrfs property set -t s {} ro true \;


"""


import math, time, os, os.path, sys, statvfs

# DATE_FORMAT = '%Y%m%d-%H%M%S' # date format used for directories to clean
DATE_FORMAT = '%Y%m%d-%H%M%S'

DEFAULT_KEEP_BACKUPS = 10

# find TIME_SCALE: t < 2**32 => e**(t/c) < 2**32
TIME_SCALE = math.ceil(float((2**32)/math.log(2**32)))

def timef(x):
    """make value inverse exponential in the time passed"""
    try:
        v = math.exp(
            time.mktime(
                time.strptime(
                    os.path.split(x)[1],
                    DATE_FORMAT))
            /TIME_SCALE)
    except:
        v = None
    return v

def sorted_value(dirs):
    if len(dirs) <= 0:
        return dirs
    else:
        return _sorted_value(dirs)

def _sorted_value(dirs):
    """Iterate dirs, sorted by their relative value when deleted"""
    def poles(items):
        """Yield (items[0], items[1]), (items[1], items[2]), ... (items[n-1], items[n])"""
        rest = iter(items)
        last = rest.next()
        for next in rest:
            yield (last, next)
            last = next
    def all_but_last(items):
        """Yield items[0], ..., items[n-1]"""
        rest = iter(items)
        last = rest.next()
        for x in rest:
            yield last
            last = x

    # Remaining candidates for yield,
    # except the "max" one (latest)
    candidates = dict(
        all_but_last((x, xf)
                     for xf, x
                     in sorted((timef(y), y) for y in dirs)
                     if xf))
    # Keep going as long as there is anything to remove
    while len(candidates) > 1:
        # Get candidates ordered by timestamp (as v is monitonic in timestamp)
        remain = sorted((v,k) for k,v in candidates.iteritems())
        # Find the "amount of information we loose by deleting the
        # latest of the pair"
        diffs = list((to_tf - frm_tf, frm, to)
                               for ((frm_tf, frm), (to_tf, to))
                               in poles(remain))
        # Select the least important one
        mdiff, mfrm, mto = min(diffs)

        del candidates[mto] # That's not a candidate any longer, it's gonna go
        yield mto

    # also, we must delete the last entry
    yield candidates.iterkeys().next()

def freespace(path):
    st = os.statvfs(path)
    return st[statvfs.F_BFREE] * st[statvfs.F_FRSIZE]

class Operations:
    def __init__(self, path, trace = None):
        self.tracef = trace
        self.path = path
    def check_call(self, args, shell=False):
        cmd_str = " ".join(args)
        self.trace("EXEC: " + cmd_str)
        import subprocess
        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            shell=shell)
        stdout = p.communicate()[0]
        self.trace(stdout)
        if p.returncode != 0:
            raise Exception("failed %s" % cmd_str)
        return stdout # return the content
    def sync(self, dir):
        # syncing to be sure the operation is on the disc
        args = ["sudo", "btrfs", "filesystem", "sync", dir]
        self.check_call(args)
    def unsnap(self, dir):
        args = ["sudo", "btrfs", "subvolume", "delete",
                os.path.join(self.path, dir)]
        self.check_call(args)
    def freespace(self):
        return freespace(self.path)
    def listdir(self):
        return [d for d in os.listdir(self.path) if timef(d)]

    def listremote_dir(self, receiver, receiver_path):
        args = ["ssh", receiver, "ls -1 " + receiver_path]
        return [d for d in self.check_call(args).splitlines() if timef(d)]

    def snap(self, path):
        #yt: changed to readonly snapshots
        newdir = os.path.join(self.path, self.datestamp())
        args = ["sudo", "btrfs", "subvolume", "snapshot", "-r",
                path,
                newdir]
        self.check_call(args)
        self.sync(self.path) #yt: make sure the new snap is on the disk
        return newdir #yt: return the latest snapshot
    def datestamp(self):
        return time.strftime(DATE_FORMAT, time.gmtime(None))
    def trace(self, *args, **kwargs):
        f = self.tracef
        if f:
            f(*args, **kwargs)
    def send_single(self, dir, receiver, receiver_path):
        args = ["sudo btrfs send -v " +
                os.path.join(self.path, dir) +
                "| pv -brt | ssh " +
                receiver +
                " \' sudo btrfs receive " + receiver_path + " \'"]
        # TODO: breakup the pipe stuff and do it without shell=True, currently it has problems with pipes :(
        self.check_call(args,shell=True)

    def send_withparent(self, parent_snap, snap, receiver, receiver_path):
        args = ["sudo btrfs send -v -p " +
                os.path.join(self.path, parent_snap) + " " +
                os.path.join(self.path, snap) +
                " | pv -brt | " + "ssh " +
                receiver +
                " \'sudo btrfs receive -v " +
                receiver_path +  " \'"
                ]
        self.check_call(args,shell=True)
    def link_current(self, receiver, receiver_path, snap, link_target):
        args = ["ssh", receiver, "sudo ln -sfn \'" + os.path.join(receiver_path,snap) + "\' " + link_target ]
        self.check_call(args)


class FakeOperations(Operations):
    def __init__(self,
                 path,
                 trace = None,
                 dirs = None,
                 space = None,
                 snap_space = None):
        Operations.__init__(self, path = path, trace = trace)
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
        self.trace("snap(%s)", path)
        self.dirs[self.datestamp()] = self.snap_space

    def unsnap(self, dir):
        self.trace("unsnap(%s)", dir)
        v = self.dirs[dir]
        self.space += v
        del self.dirs[dir]
    def listdir(self):
        self.trace("listdir() = %s", self.dirs.keys())
        return self.dirs.iterkeys()
    def listremote_dir(self, receiver, receiver_path):
        self.trace("listremotedir() r=%s, rp=%s", receiver, receiver_path)
        return ['20101201-030000', '20101201-040000', '20101201-050000']
    def freespace(self):
        self.trace("freespace() = %s", self.space)
        return self.space
    def check_call(self, args, shell=False):
        cmd_str = " ".join(args)
        self.trace("EXEC: " + cmd_str)

def cleandir(operations, targets):
    """Perform actual cleanup using 'operations' until 'targets' are met"""
    trace = operations.trace
    keep_backups = targets.keep_backups
    target_fsp = targets.target_freespace
    target_backups = targets.target_backups
    was_above_target_freespace = None
    was_above_target_backups = None
    last_dirs = []
    def first(it):
        for x in it:
            return x

    while True:
        do_del = None
        dirs = sorted(operations.listdir())
        dirs_len = len(dirs)
        if dirs_len <= 0:
            raise Exception("No more directories to clean")
            break
        elif sorted(dirs) == last_dirs:
            raise Exception("No directories removed")
            break
        else:
            last_dirs = dirs

        if keep_backups is not None:
            if dirs_len <= keep_backups:
                print "Reached number of backups to keep: ", dirs_len
                break

        if target_fsp is not None:
            fsp = operations.freespace()
            #print "+++ ", fsp, target_fsp, fsp >= target_fsp
            if fsp >= target_fsp:
                if (was_above_target_freespace or was_above_target_freespace is None):
                    trace("Satisfied freespace target: %s with %s",
                          fsp, target_fsp)
                    was_above_target_freespace = False
                if do_del is None:
                    do_del = False
            else:
                if was_above_target_freespace is None:
                    was_above_target_freespace = True
                do_del = True

        if target_backups is not None:
            if dirs_len <= target_backups:
                if (was_above_target_backups or was_above_target_backups is None):
                    trace("Satisfied target number of backups: %s with %s",
                          target_backups, dirs_len)
                    was_above_target_backups = False
                if do_del is None:
                    do_del = False
            else:
                if was_above_target_backups is None:
                    was_above_target_backups = True
                do_del = True

        if not do_del:
            break

        next_del = first(sorted_value(dirs))
        if next_del is None:
            trace("No more backups left")
            break
        else:
            operations.unsnap(next_del)

def transfer(operations, target_host, target_dir, link_dir):
    """Transfer snapshots to remote host"""

    trace = operations.trace

    # find out what kind of snapshots exist on the remote host
    targetsnaps = set(operations.listremote_dir(target_host, target_dir))
    localsnaps = set(operations.listdir())

    if len(localsnaps) == 0:
        #nothing to do here, no snaps here
        return

    parents = targetsnaps.intersection(localsnaps)

    # no parent exists so
    if len(parents) == 0:
        # start transferring the oldest snapshot
        # by that snapbtrex will transfer all snapshots that have been created
        operations.send_single( min(localsnaps), target_host, target_dir)
        parents.add(min(localsnaps))


    # parent existing, use the latest as parent
    parent =  max(parents)
    nparent = parent

    trace("last possible parent = %s", parent)

    for s in sorted(localsnaps):
        if s > parent:
            trace("transfer: parent=%s snap=%s", nparent, s)
            operations.send_withparent(nparent, s, target_host, target_dir)
            if link_dir is not None:
                operations.link_current(target_host, target_dir, s, link_dir)
            # advance one step
            nparent=s


def log_trace(fmt, *args, **kwargs):
    tt = time.strftime(DATE_FORMAT, time.gmtime(None)) + ": "
    if args is not None:
        print  tt + (fmt % args)
    elif kwargs is not None:
        print tt + (fmt % kwargs)
    else:
        print tt + (fmt)

def default_trace(fmt, *args, **kwargs):
    if args is not None:
        print fmt % args
    elif kwargs is not None:
        print fmt % kwargs
    else:
        print fmt

def main(argv):
    def args():
        import argparse
        class Space(int):
            @staticmethod
            def parse_target_freespace(target_str):
                import re
                mods = {
                    None: 0,
                    'K': 1,
                    'M': 2,
                    'G': 3 }
                form = "([0-9]+)(%s)?" % \
                    "|".join(x for x in mods.iterkeys() if x is not None)
                m = re.match(form, target_str, re.IGNORECASE)
                if m:
                    val, mod = m.groups()
                    return int(val) * 1024**mods[mod]
                else:
                    raise "Invalid value: %s, expected: %s" % (target_str, form)

            def __nonzero__(self):
                return True

            def __init__(self, value):
                self.origin = value
            def __new__(cls, value=0):
                if isinstance(value, (str, unicode)):
                    value = Space.parse_target_freespace(value)
                return super(Space, cls).__new__(cls, value)
            def __str__(self):
                if isinstance(self.origin, int):
                    return str(self.origin)
                else:
                    return "%s[%s]" % (self.origin, int(self))

        parser = argparse.ArgumentParser(
            description = 'keeps btrfs snapshots for backup',
            #formatter_class = argparse.ArgumentDefaultsHelpFormatter
            )

        parser.add_argument('--path', '-p',
            metavar = 'PATH',
            help = 'Path for snapshots and cleanup',
            default = '.')

        target_group = parser.add_argument_group(
            title = 'Cleanup',
            description = 'Try to cleanup until all of the targets are met.')

        target_group.add_argument('--target-freespace', '-F',
            dest = 'target_freespace',
            metavar = 'SIZE',
            default = None,
            type = Space,
            help = '''Cleanup PATH until at least SIZE is free. SIZE is #bytes, or given with K, M, G or T respectively for kilo, ...''')

        target_group.add_argument('--target-backups', '-B',
            dest='target_backups',
            metavar = '#',
            type = int,
            help ='Cleanup PATH until at most B backups remain')

        target_group.add_argument('--keep-backups', '-K',
            metavar = '#',
            type = int,
            default = DEFAULT_KEEP_BACKUPS,
            help = 'Stop cleaning when K backups remain')

        snap_group_x = parser.add_argument_group(title = 'Snapshotting')
        snap_group = parser.add_mutually_exclusive_group(required=False)

        snap_group.add_argument('--snap', '-s',
            metavar = 'SUBVOL',
            default = '/',
            help = 'Take snapshot of SUBVOL on invocation')

        snap_group.add_argument('--no-snap', '-S',
            dest = 'snap',
            help = 'Disable snapshot taking',
            action = 'store_const',
            const = None)

        parser.add_argument('--test',
            help = 'Execute built-in test',
            action = 'store_true')

        parser.add_argument('--explain',
            help = 'Explain what %(prog)s does (and stop)',
            action = 'store_true')

        parser.add_argument('--verbose',
            help = 'Verbose output',
            action = 'store_true')

        transfer_group = parser.add_argument_group(
            title = 'Transfer',
            description = 'Transfer Snapshots to other hosts. It is assumed that the user running the script is run can connect to the remote host via keys and without passwords. See --explain for more info')

        transfer_group.add_argument('--remote-host',
            metavar = 'HOST',
            dest = 'remote_host',
            help = 'Transfer to target host via ssh.')

        transfer_group.add_argument('--remote-dir',
            metavar = 'PATH',
            dest = 'remote_dir',
            help = 'Transfer the snapshot to this directory on the target host')

        transfer_group.add_argument('--remote-link',
            metavar = 'PATH',
            dest = 'remote_link',
            help = 'link the transferred snapshot to this PATH')


        pa = parser.parse_args(argv[1:])
        return pa, parser
    pa, parser = args()

    if pa.verbose:
        if sys.stdout.isatty():
            trace = default_trace
        else:
            trace = log_trace
    else:
        trace = None

    if pa.explain:
        sys.stdout.write(__doc__)
        return 0

    if pa.target_freespace is None and pa.target_backups is None:
        parser.error("Set a target, either with: \n"
                     "  --target-freespace, or\n"
                     "  --target-backups")

    if pa.test:
        operations = FakeOperations(path = pa.path,
                                    trace = trace,
                                    dirs = {
                '20101201-000000': 0,
                '20101201-010000': 1,
                '20101201-020000': 2,
                '20101201-030000': 3,
                '20101201-040000': 4,
                '20101201-050000': 5,
                '20101201-060000': 6,
                '20101201-070000': 7,
                '20101201-080000': 8,
                },
                                    space = 5)

    else:
        operations = Operations(path = pa.path, trace = trace)

    if pa.snap:
       operations.snap(path = pa.snap)

    if not (pa.remote_host is None and pa.remote_dir is None ):
        transfer(operations, pa.remote_host, pa.remote_dir, pa.remote_link)

    cleandir(operations = operations, targets = pa)

if "__main__" == __name__:
    sys.exit(main(sys.argv))
