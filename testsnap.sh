#!/usr/bin/env bash

LIMG="local.test.img"
LMNT="btrfs.test.local"

test_error() {
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

setup() {
  truncate -s 140M $LIMG

  mkfs.btrfs $LIMG

  mkdir $LMNT

  mount -o loop $LIMG $LMNT

  btrfs subvolume create $LMNT/path
  mkdir $LMNT/.snapshot
  mkdir $LMNT/.sync

  touch $LMNT/path/file.file
}

cleanup (){

  umount $LMNT
  rmdir $LMNT
  rm $LIMG
}


test_local_sync(){
  for i in {1..20}
  do
    ./snapbtrex.py --path "./$LMNT/.snapshot/" --snap "./$LMNT/path/"  --target-backups 10 --verbose --sync-target "./$LMNT/.sync/" --sync-keep 5
    sleep 1
  done
  # should be 10 dirs in .snapshot
  X=$(find ./$LMNT/.snapshot/* -maxdepth 0 -type d | wc -l)
  test_equal "$X" 10 "Keep Snapshot "

  # and 5 dirs in sync
  Y=$(find ./$LMNT/.sync/* -maxdepth 0 -type d | wc -l)
  test_equal "$Y" 5 "Sync keep"
}

setup

test_local_sync

cleanup


