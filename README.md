# snapbtrex
snapbtrex is a small utility that keeps snapshots of btrfs filesystems
and optionally send it to a remote system.

The script came originally from <https://btrfs.wiki.kernel.org/index.php/SnapBtr> .
This is an extended version which is capable of transferring snapshots to remote
systems.

You can run it regularly (for example in a small script in
cron.hourly or in crontab), or once in a while, to maintain an "interesting" (see
below) set of snapshots (backups). You may manually add or remove
snapshots as you like, use `snapbtrex.DATE_FORMAT` (in GMT) as
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
snapshots, (newer, older). snapbtrex will remove the older of the two
snapshots in the space that have the lowest score.

The scoring mechanism integrates e^x from (now-newer) to (now-older)
so, new pairs will have high value, even if they are tightly packed,
while older pairs will have high value if they are far apart.

Alternatively you can also keep only the latest snapshots via `--keep-only-latest` or set a maximum age for your snapshots with the `--max-age` parameter.

## Transferring Snapshots to Remote Host

snapbtrex uses the btrfs send and receive commands to transfer
snapshots from a sending host to a receiving host via ssh. Using `--ssh-port`, 
you can specify the port on which such ssh connections will be 
attempted. 

Both hosts have to be prepared as in the setup instructions if
you want to call the script via cronjob. You can always call snapbtrex
as standalone script if you have appropriate rights.

Specify your target host via  `--remote-host` and the directory with
the `--remote-dir` options. Both options have to be present. The target directory
has to be located within a btrfs file system and it has to be mounted via the
root volume, or else btrfs might fail to receive snapshots.

### Setup instructions
For transfer backups with ssh within an automated script (cronjob) you have to
prepare the systems with the following steps.

1\. create user `snapbtr` on both systems
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

Minimum content is this for receiving snapshots on a remote system:
```
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs receive*
```

If you want to link the latest transferred snapshot remotely with `--remote-link`
then you will need another line (adopt path to your specific path):

```
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/ln -sfn /path/to/backups/* /path/to/current/current-link
```

If you want remote pruning of snapshots via `--remote-keep` option, then add this:
```
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs subvolume delete*
```

4\. Create a sudoers include file on the sending machine

File: `/etc/sudoers.d/90_snapbtrsnd`

Contents:
```
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs send*
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs subvolume*
snapbtr ALL=(root:nobody) NOPASSWD:NOEXEC: /bin/btrfs filesystem sync*
```
Hint 1: For a more secure setup you should include the specific paths at the
sudoers files.

Hint 2: On some Linux flavors you might find the btrfs tools in `/sbin/btrfs`
opposed to `/bin/btrfs`, the sudoers files have to reflect that. Try using `which btrfs` to find out the full path to your `btrfs`.


## Migrating from SnapBtr

If you created snapshots with [snapbtr](https://btrfs.wiki.kernel.org/index.php/SnapBtr)
then those snapshots were created as read/write snapshots. The sending of snapshots
to remote hosts demands that those snaps are read only. You can change rw snaps
to ro snaps in the directory of the snapshots via:

```sh
sudo find . -maxdepth 1 -type d -exec btrfs property set -t s {} ro true \;
```

## Examples

### Shell

Snapshot a volume and keep 20 versions:
```sh
sudo snapbtrex.py --snap /mnt/btrfs/@subvol1/ --path /mnt/btrfs/.mysnapshots/subvol1/ --target-backups 20
```

### Crontab

Snapshot and transfer to remote host every day at 4:10 am, keep 52 snapshots on
the origin host (keeps all remote backups, unless you delete them manually)
```
10 4    * * *   snapbtr /opt/snapbtrex/snapbtrex.py --snap /mnt/btrfs/@subvol1/ --path /mnt/btrfs/.mysnapshots/subvol1/ --target-backups 52 --verbose --remote-host 123.45.56.78 --remote-dir /mnt/btrfs/.backup/subvol1/  >> /var/log/snapbtrex.log
```


Snapshot and transfer to remote host every day at 4:20 am, keep 10 snapshots on
the origin host and keep only 50 snapshots on the remote host.
```
20 4    * * *   snapbtr /opt/snapbtrex/snapbtrex.py --snap /mnt/btrfs/@subvol2/ --path /mnt/btrfs/.mysnapshots/subvol2/ --target-backups 10 --verbose --remote-host 123.45.56.78 --remote-dir /mnt/btrfs/.backup/subvol2/ --remote-keep 50 >> /var/log/snapbtrex.log
```
