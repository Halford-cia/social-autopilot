# Social autopilot

The Mini prepares a seven-day feed calendar for Stay Eden (Instagram and Facebook) and Acacia Roofing (Instagram, Facebook and Google Business Profile). Each brand has its own approval history and an `autoPublish` switch that ships set to `false`.

## On the phone

Open the private GitHub issue titled **Social · [brand] · approve [Monday]**. Read the seven captions and the channel-specific ending. Tap the photo links. Copy the exact `/approve …` line into a new comment while signed into Hank’s GitHub account. The Mini checks the issue every 15 minutes. Closing an issue holds any remaining submissions. Previously scheduled posts can be changed or canceled in Blotato’s calendar.

Sunday’s cycle prepares Monday through Sunday in Central Time. An initial midweek start prepares the next full week. Past slots expire. The second approval leaves manual approval enabled. To enable automatic scheduling for a brand, set only its `autoPublish` to `true` in the local config. It takes effect for later weeks after two distinct manually approved weeks have complete scheduling receipts. Set it back to `false` to require a fresh comment for subsequent batches.

## Mini setup

1. Create or sign into the Blotato account that will hold both brands. Under **Accounts → Connect account**, connect each professional Instagram account with its own Instagram login. Connect Facebook using a login with admin access to each intended Page, select both Pages, and keep the authorizing login connected. Connect Acacia’s Google Business Profile and select its location. Enable comments, DMs and analytics for the relevant accounts. Confirm that Stay Eden’s first Instagram profile link goes to its stay collection. [Connect Instagram](https://help.blotato.com/settings/social-accounts/instagram.md) · [Connect Facebook](https://help.blotato.com/settings/social-accounts/facebook.md) · [Destinations and subaccounts](https://help.blotato.com/rest-api-reference/accounts.md)
2. Under **Settings → API**, copy the complete Blotato key. In macOS **Keychain Access**, create a new password item in the Mini user’s login keychain with item/service name `social-autopilot/BLOTATO_API_KEY` and the key as the password. Create a second item named `social-autopilot/COMPANYCAM_API_KEY` for the read-only CompanyCam key with access to tags and photos. On first use, grant the intended user’s `security` process access via the Keychain prompt. A process environment variable with the corresponding uppercase name takes precedence. Keep both values out of shell commands, source files, plists and logs. [Blotato API keys](https://help.blotato.com/settings/api-keys.md)
3. Use the Mini’s logged-in user. Have Python 3.11+ with zoneinfo, Node 22+, Git, GitHub CLI and local Ollama available. With Ollama running on loopback, pull the vision model with `ollama pull gemma3:4b`. The Mini reads the reference repositories and STR brief at the paths in the example config. Update the local remote-tracking refs with `git -C ~/acacia-roofing-systems fetch origin main` and `git -C ~/str/abenteuer-stays fetch origin main` during setup. [Local vision and structured results](https://docs.ollama.com/capabilities/structured-outputs)
4. Use a **private GitHub repository with Issues enabled** for approvals, digests and handoffs. The Mini’s `gh` login needs read/write Issues access there and read access to Acacia’s facts repo. Use Hank’s separate identity for phone approvals. On the phone, watch that repository’s Issues. Put its `owner/repo` and the verified numeric ID from `gh api users/Halford-cia --jq .id` in the local config.
5. From the reviewed social-autopilot checkout, run the following. The test and demo commands are offline. The last command reads live destination IDs and names for setup. Map each Instagram account, Facebook parent account and Page, and Acacia’s Google account and full `accounts/…/locations/…` ID to the corresponding fields. Confirm the names line up with the intended brands.

```sh
cd ~/social/social-autopilot
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s test
.venv/bin/python -m social.cli demo --week 2026-11-30
data_dir="$HOME/Library/Application Support/social-autopilot"
mkdir -p -m 700 "$data_dir"
cp -n config.example.json "$data_dir/config.json"
chmod 600 "$data_dir/config.json"
.venv/bin/python -m social.cli accounts
```

### Enable the schedule

After completing config, run these commands **when ready to activate the live inbox and weekly previews**. Loading the LaunchAgent starts the first cycle. New eligible Stay Eden questions can receive replies from that point. The approval setting governs outbound feed posts. This is an example plist following the Mini’s per-user `RunAtLoad`, `StartInterval`, PATH and log pattern.

```sh
cd ~/social/social-autopilot
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/mini"
agent_plist="$HOME/Library/LaunchAgents/com.acacia.social-autopilot.plist"
sed -e "s|__REPO__|$PWD|g" -e "s|__LOG__|$HOME/Library/Logs/mini|g" \
  tools/com.acacia.social-autopilot.plist.example > "$agent_plist"
plutil -lint "$agent_plist"
launchctl bootstrap "gui/$(id -u)" "$agent_plist"
launchctl print "gui/$(id -u)/com.acacia.social-autopilot"
```

To pause the Mini, run `launchctl bootout "gui/$(id -u)/com.acacia.social-autopilot"`. Open Blotato’s calendar to cancel previously accepted posts if needed. Keep `~/Library/Application Support/social-autopilot/ledger/` backed up privately. Its durable write intents and provider receipts prevent duplicate submissions after a crash. A second process exits when the ledger lock is held.

## Content, media and inbox

- The calendar rotates 17 reviewed cards per brand with the launch kit’s voice and Central Time windows. Feed images and text fit the publishing API. Prices, review counts and testimonials are excluded. The booking CTA uses the collection linked in the launch kit. Each Acacia card cites a section of FACTS.md in `social/planner.py`.
- Before a batch is scheduled, the Mini checks the pinned facts and STR brief for their respective brands. Acacia also compares the local facts and audit blobs to current upstream main and runs the source repo’s own banned-claim scanner on complete captions. If a source changes, review its diff, update affected cards and the source pins, and run the tests. Recheck pending previews.
- Stay Eden uses its property’s full-size files under `public/images` in the stays repo. For Acacia, the PM’s existing finished-job selection uses the two tags `after` and `drone`. The Mini checks exact per-photo tags, metadata, visibility and original pixels. It rejects location metadata, unfamiliar labels and descriptive metadata, then uses a local vision model to screen for faces, house numbers, street signs, readable text, plates and identifiable homes. It requires a clear subject and an explicit all-clear for every flag. Only re-encoded JPEG pixels go to Blotato. Automated detection can miss details, so inspect the first two weeks’ photos on the phone. If media is unavailable or uncertain, Instagram is held and eligible text destinations retain their caption. [CompanyCam photos](https://docs.companycam.com/reference/listphotos) · [Photo tags](https://docs.companycam.com/reference/listphototags) · [Blotato media uploads](https://help.blotato.com/rest-api-reference/publish-post/upload-media-v2-media.md)
- Stay Eden replies to complete booking, availability or area questions. Date requests go to the booking link. The brief gives no standard check-in time, so these questions go to the host. Compound requests, rich messages, nested comments and uncertain identity go to human review. Acacia produces suggested drafts only. Handoff issues give a channel and time to find the original privately in Blotato. Close a DM handoff after resolving it to release its thread. The inbox starts with new arrivals on activation. [Comments](https://help.blotato.com/rest-api-reference/comments.md) · [Messages](https://help.blotato.com/rest-api-reference/messages.md)
- The Monday issue covers the prior Monday through Sunday in Central Time and catches up after downtime. It sums reported post reach, shows how many posts have a measurement, ranks the top three, and suggests the next test. Shared audiences may be counted across posts. The documented IG/FB endpoints expose post metrics without account-level follower history. Follower change is labeled unavailable. Google Business Profile analytics are unavailable. [Post list and snapshots](https://help.blotato.com/rest-api-reference/publish-post/list-posts.md) · [Analytics](https://help.blotato.com/rest-api-reference/analytics.md)

## Delivery and recovery

The approval token covers the brand, week, exact captions, times, media and destination IDs. The Mini checks the current private preview and the approving comment’s immutable author ID. It sends one `POST /v2/posts` per eligible destination with `scheduledTime` at the request body root. Accepted submission IDs are polled through the status endpoint. A rate-limited scheduling request retries on a later cycle. An ambiguous write stays on hold and produces an issue. Inspect the matching brand, time and destination in the Blotato calendar before completing any missing post manually. Apply the same procedure in the Blotato inbox for a held reply. The Mini never replays an ambiguous write automatically. [Publish](https://help.blotato.com/rest-api-reference/publish-post.md) · [Submission status](https://help.blotato.com/rest-api-reference/publish-post/get-post.md)
