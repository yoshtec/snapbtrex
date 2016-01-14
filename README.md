# snapbtrex
snapbtrex is a small utility that keeps snapshots of btrfs filesystems
and optionally send it to a remote system.

The script came originally from https://btrfs.wiki.kernel.org/index.php/SnapBtr it is an extended version wich is capable of transferring snapshots to remote systems.

You can run it regularly (for example in a small script in
cron.hourly), or once in a while, to maintain an "interesting" (see
below) set of snapshots (backups). You may manually add or remove
snapshots as you like, use 'snapbtr.DATE_FORMAT' (in GMT) as
snapshot-name.

It will keep at most `--target-backups` snapshots and ensure that
`--target-freespace` is available on the file-system by selecting
snapshots to remove.

Using `--keep-backups`, you can ensure that at least some backups are
kept, even if `--target-freespace` cannot be satisfied.

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


## Transferring Snapshots to Remote Host

snapbtrex uses the btrfs send and recieve commands to transfer
snapshots from a sendin host to a receiving host.

Both hosts have to be prepared as in the setup instructions if
you want to call the script via cronjob. You can always call snapbtrex 
as standalone script if you have appropiate rights.

### Setup instructions
transfer with backups with ssh

1\. create user ``snapbtr` on both systems
```sh
sudo adduser snapbtr
```

2\. generate ssh key on sender and copy public key to receiving machine

```sh
su - snapbtr

ssh-keygen -t rsa

ssh-copy-id snapbtr@123.45.56.78
```

3\. create a sudoers include file at the receiving machine (use `sudo visudo`)

File: `/etc/sudoers.d/90_snapbtrrcv`

Contents:
```
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /sbin/btrfs receive*
```

Hint: on some Linux flavors you might find the btrfs tools in `/bin/btrfs` opposed to `/sbin/btrfs`, the sudoers files have to reflect that.

4\. Create a sudoers include file on the sending machine

File: `/etc/sudoers.d/90_snapbtrsnd`

Contents:
```
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /sbin/btrfs send*
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /sbin/btrfs snapshot*
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /sbin/btrfs filesystem sync*
```


## Precautions
If you created your snapshots with an old version of snapbtr than those
snapshots were created as read/write snapshots. The sending of snapshots
to remote hosts demands that those snaps are read only. You can change rw snaps
to ro snaps in the directory of the snapshots via:

```sh
sudo find . -maxdepth 1 -type d -exec btrfs property set -t s {} ro true \;
```

## Crontab Example

Snapshot and transfer to remote host every day at 4:10 am, keep 52 snapshots on the origin host.
```
10 4    * * *   snapbtr /opt/snapbtr/snapbtrex.py --path /mnt/btrfs/.mysnapshots/subvol1/ --snap /mnt/btrfs/@subvol1/ --target-backups 52 --verbose --remote-host 123.45.56.78 --remote-dir /mnt/btrfs/.backup/subvol1/  >> /var/log/snapbtrex.log
```
