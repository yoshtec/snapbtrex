#!/usr/bin/env bash
#set -x #Echo commands for debugging

LIMG="local.test.img"
LMNT="btrfs.test.local"
SUBVOLUME="./$LMNT/subvolume"
SNAPSHOT="./$LMNT/.snapshot"

RESULT=0

header() {
  echo -e "\e[1m\e[44mHEADER: $1 \e[0m"
}

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
    test_equal "$?" 0 "Run: $i"
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
    test_equal "$?" 0 "Run: $i"
    sleep 1
  done

  FIRST=$(find $SNAPSHOT/* -maxdepth 0 -type d | sort)
  echo "First snapshots:"
  echo "$FIRST"


  for i in {1..10}
  do
    ./snapbtrex.py --path "$SNAPSHOT" --snap "$SUBVOLUME" --target-backups 10 --keep-only-latest --verbose
    test_equal "$?" 0 "Run: $i"
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

test_local_size(){
  for i in {1..15}
  do
    head -c 10M </dev/urandom >"$SUBVOLUME/randomfile.file"
    test_equal "$?" 0 "Run: $i adding bigger file"
    ./snapbtrex.py --path "$SNAPSHOT" --snap "$SUBVOLUME" --verbose --target-freespace 50M --keep-backups 1
    test_equal "$?" 0 "Run: $i Snapshot"
    df "./$LMNT/"
    sleep 1
  done
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

header "Test local Sync"
setup_btrfs
test_local_sync
cleanup_btrfs

header "Test latest"
setup_btrfs
test_local_latest
cleanup_btrfs

header "Test Size"
setup_btrfs
test_local_size
cleanup_btrfs


exit $RESULT

