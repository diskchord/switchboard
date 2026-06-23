# F-Droid Preparation

Switchboard's Android wrapper is prepared for F-Droid-style builds with:

- Application ID: `com.alexanderpeppe.switchboard`
- License: Apache-2.0
- Build path: `mobile/android`
- Build command: `gradle -p mobile/android assembleRelease`
- Upstream metadata: `fastlane/metadata/android/en-US/`

The Android app can now be built without `SWITCHBOARD_APP_URL`; in that generic mode it asks the user for their own Switchboard server on first launch. Local or private sideload builds may still set `SWITCHBOARD_APP_URL` in `mobile/android/local.properties`.

Likely review note: Switchboard is self-hosted, but its core phone features are normally configured against SMS/voice/fax providers such as Telnyx or Twilio. F-Droid reviewers may mark this with a network-service anti-feature depending on how the app is submitted.

Before opening an fdroiddata merge request:

- Publish the source repository and tag the release commit as `v0.7.0`.
- Confirm no private server URLs, phone numbers, credentials, or message data are committed.
- Build once from a clean clone without `mobile/android/local.properties`.
- Add screenshots under the upstream Fastlane metadata tree if desired.
- Fill in the fdroiddata metadata template with the final public repository URL.
