#!/usr/bin/env bash

LIMG="local.test.img"
LMNT="btrfs.test.local"
SUBVOLUME="./$LMNT/subvolume"
SNAPSHOT="./$LMNT/.snapshot"

RESULT=0

test_error() {
  # Increase Error count
  RESULT+=1
  # see here: https://misc.flogisoft.com/bash/tip_colors_and_formatting
  echo -e "\e[1m\e[41mERROR: \e[0m $3, Result: $1, Expected: $2"
}

test_ok() {
  echo -e "\e[1m\e[42mPASSED: \e[0m $3, Result: $1, Expected: $2"
}

test_equal() {
  if [ "$1" -eq "$2" ]
  then
    test_ok "$1" "$2" "$3"
  else
    test_error "$1" "$2" "$3"
  fi
}

setup_btrfs() {
  truncate -s 140M $LIMG

  mkfs.btrfs $LIMG

  mkdir $LMNT

  mount -o loop $LIMG $LMNT

  btrfs subvolume create "$SUBVOLUME"
  mkdir "$SNAPSHOT"
  mkdir "$LMNT/.sync"

  touch "$SUBVOLUME/file.file"
  head -c 1M </dev/urandom >"$SUBVOLUME/randomfile.file"

}

cleanup_btrfs (){
  umount $LMNT
  rmdir $LMNT
  rm $LIMG
}


test_local_sync(){
  for i in {1..20}
  do
    ./snapbtrex.py --path "$SNAPSHOT" --snap "$SUBVOLUME" --target-backups 10 --verbose --sync-target "./$LMNT/.sync/" --sync-keep 5
    sleep 1
  done

  # should be 10 dirs in .snapshot
  X=$(find $SNAPSHOT/* -maxdepth 0 -type d | wc -l)
  test_equal "$X" 10 "Keep Snapshot "

  # and 5 dirs in sync
  Y=$(find ./$LMNT/.sync/* -maxdepth 0 -type d | wc -l)
  test_equal "$Y" 5 "Sync keep"
}

test_local_latest(){
  for i in {1..5}
  do
    ./snapbtrex.py --path "$SNAPSHOT" --snap "$SUBVOLUME" --target-backups 10 --keep-only-latest --verbose
    sleep 1
  done

  FIRST=$(find $SNAPSHOT/* -maxdepth 0 -type d | sort)
  echo "First snapshots:"
  echo "$FIRST"


  for i in {1..10}
  do
    ./snapbtrex.py --path "$SNAPSHOT" --snap "$SUBVOLUME" --target-backups 10 --keep-only-latest --verbose
    sleep 1
  done

  X=$(find $SNAPSHOT/* -maxdepth 0 -type d | wc -l)
  test_equal "$X" 10 "Keep Snapshot "

  # should be 10 dirs in .snapshot
  LAST=$(find $SNAPSHOT/* -maxdepth 0 -type d | sort)
  echo "Last snapshots:"
  echo "$LAST"

  count=$(echo "${FIRST[@]}" "${LAST[@]}" | sed 's/ /\n/g' | sort | uniq -d | wc -l)
  test_equal "$count" 0 "keep latest"
}

####
# Main
####

# exit with error if not run as root
if [[ $(id -u) -ne 0 ]] ; then
  test_error "$(id -u)" 0 "running as root"
  echo "testing needs privileged access to btrfs filesystem actions. please run as root"
  exit $RESULT
fi

# in case the last didn't clean all
cleanup_btrfs

setup_btrfs
test_local_sync
cleanup_btrfs

setup_btrfs
test_local_latest
cleanup_btrfs

exit $RESULT

