#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
r8=target/tools/r8-8.3.37.jar
android_jar=/usr/lib/android-sdk/platforms/android-23/android.jar
printf '%s  %s\n' 59753e70a74f918389cc87f1b7d66b5c0862932559167425708ded159e3de439 "$r8" | sha256sum -c -
mkdir -p target/shutdown
work=$(mktemp -d target/shutdown/build.XXXXXX)
javac --release 8 -cp "$android_jar" -d "$work/classes" native/shutdown/RuntimeShutdown.java native/shutdown/RuntimeNetworkProbe.java native/shutdown/RuntimeTask.java native/shutdown/RuntimeCatalog.java
jar cf "$work/input.jar" -C "$work/classes" .
java -cp "$r8" com.android.tools.r8.D8 --min-api 33 --lib "$android_jar" --output "$work" "$work/input.jar"
jar cf target/shutdown/anvildroid-shutdown.jar -C "$work" classes.dex
