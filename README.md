# Social autopilot

The Mini prepares a seven-day feed calendar for Stay Eden (Instagram and Facebook) and Acacia Roofing (Instagram, Facebook and Google Business Profile). Each brand has its own approval history and an `autoPublish` switch that ships set to `false`.

## On the phone

Open the private GitHub issue titled **Social · [brand] · approve [Monday]**. Check the **Destinations** list first: it shows the Instagram handle, Facebook Page name and (Acacia) Google Business location name that Blotato reports for the configured IDs. If any name belongs to the other brand, do not approve. A destination shown as **not verified, held** gets nothing that week: no row lists it and nothing is ever sent there, even after your approval. Read the seven captions and the channel-specific ending. Tap the photo links. Copy the exact `/approve …` line into a new comment while signed into Hank’s GitHub account. Only a new, never-edited comment by Hank counts: a comment edited by anyone (Hank or a repository moderator) is ignored, so post a fresh one instead of fixing a typo. Approving also confirms those accounts by their IDs as well as their names: a different Page or location with the same name is a change that needs a fresh approval. The Mini checks the issue every 15 minutes.

Posts stay on the Mini until about three hours before their slot, and only then go to Blotato (so Blotato’s calendar shows a post only from that point). In those last three hours each cycle checks again that the week is still approved (or, for an automatic week, that `autoPublish` is still `true`), that the issue is still open with the same preview, that the destination is the approved one and that the photo is still eligible. So closing the issue, setting `autoPublish` to `false` or a PM withdrawing a photo days ahead stops every later post of that week that has not gone out yet. A post already submitted inside its last three hours is changed or canceled in Blotato’s calendar. The Mini must be awake in that window: a post whose window passes while the Mini is off or asleep expires and is not posted late.

Sunday’s cycle prepares Monday through Sunday in Central Time. An initial midweek start prepares the next full week. Past slots expire. The second approval leaves manual approval enabled. To enable automatic scheduling for a brand, set only its `autoPublish` to `true` in the local config. It takes effect for later weeks after Hank has approved two distinct earlier weeks for that brand, and only while the week's destinations (names and IDs) match the ones in Hank's last approval (otherwise the week waits for a fresh `/approve` and an issue says why). A missed slot or a delivery hold stays visible in the delivery ledger and preserves the approval. Set it back to `false` to require a fresh comment: it also stops the not-yet-submitted posts of an automatic week already under way.

## Mini setup

1. Create or sign into the Blotato account that will hold both brands. Under **Accounts → Connect account**, connect each professional Instagram account with its own Instagram login. Connect Facebook using a login with admin access to each intended Page, select both Pages, and keep the authorizing login connected. Connect Acacia’s Google Business Profile and select its location. Enable comments, DMs and analytics for the relevant accounts. Confirm that Stay Eden’s first Instagram profile link goes to its stay collection. [Connect Instagram](https://help.blotato.com/settings/social-accounts/instagram.md) · [Connect Facebook](https://help.blotato.com/settings/social-accounts/facebook.md) · [Destinations and subaccounts](https://help.blotato.com/rest-api-reference/accounts.md)
2. In the Mini user's **Keychain Access**, add three generic password items to the login keychain. Use the exact service names below and paste each value into the password field. Grant the logged-in user’s `security` command access when prompted.
   - `social-autopilot/BLOTATO_API_KEY` holds the complete key from Blotato **Settings → API**. [Blotato key setup](https://help.blotato.com/settings/api-keys)
   - `social-autopilot/COMPANYCAM_API_KEY` holds a read-only CompanyCam key with access to photos and tags. For these first two services only, the matching environment variable takes precedence.
   - `social-autopilot/ANTHROPIC_API_KEY` holds a standard workspace API key from the Claude Console. The runtime reads this item directly from Keychain only when drafting. Use a dedicated workspace if you also want to reconcile this job with the Console's spend view. [Anthropic authentication](https://platform.claude.com/docs/en/api/overview)
3. Use Python 3.11+, Node 22+, Git and GitHub CLI. Set the paths in the example config to the original STR brief and the Acacia and Stay Eden checkouts. Refresh their local refs with `git -C ~/acacia-roofing-systems fetch origin main` and `git -C ~/str/abenteuer-stays fetch origin main`. The Mini also fetches the Stay Eden checkout's `main` itself before it picks or rechecks a photo, so a photo removed on GitHub is never posted; that checkout's `origin` must be fetchable by the Mini user without a prompt. If a fetch fails, photo posts wait (an issue says so) and expire if their slot passes. Install the local photo inspector as described below.
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

### Local photo inspector

The photo check uses a one-shot llama.cpp process with Gemma 3 vision weights. The Mini inspects candidate images locally before uploading cleared pixels. On Apple Silicon, install the CLI and download the two model files once, outside the checkout. The downloads total about 3.34 GB. [llama.cpp multimodal CLI](https://github.com/ggml-org/llama.cpp/tree/master/tools/mtmd) · [Vision weights and projector](https://huggingface.co/ggml-org/gemma-3-4b-it-GGUF/tree/d0976223747697cb51e056d85c532013931fe52e)

```sh
brew install llama.cpp
vision_dir="$HOME/Library/Application Support/social-autopilot/vision"
mkdir -p -m 700 "$vision_dir"
cd "$vision_dir"
model_source=https://huggingface.co/ggml-org/gemma-3-4b-it-GGUF/resolve/d0976223747697cb51e056d85c532013931fe52e
curl -fL --retry 2 -O "$model_source/gemma-3-4b-it-Q4_K_M.gguf"
curl -fL --retry 2 -O "$model_source/mmproj-model-f16.gguf"
printf '%s\n' \
  '882e8d2db44dc554fb0ea5077cb7e4bc49e7342a1f0da57901c0802ea21a0863  gemma-3-4b-it-Q4_K_M.gguf' \
  '8c0fb064b019a6972856aaae2c7e4792858af3ca4561be2dbf649123ba6c40cb  mmproj-model-f16.gguf' | shasum -a 256 -c -
```

### Claude spending

The `claude` section of the local config sets a combined ceiling for both brands. An older config needs this section, and the `blotato` contact budget section (see Content, media and inbox), copied from the example: the tick refuses to start without them. Existing batch approvals and submission receipts keep their original content and hashes.

| Setting | Example value | Scope |
| --- | ---: | --- |
| `maxTokensPerRun` | 16000 | Input and output tokens together, across one 15-minute tick |
| `monthlySpendLimitCents` | 1000 | $10 of this Mini's Claude usage per UTC calendar month |

Setting either value to zero pauses the entire tick. Before each Messages call the Mini reserves a conservative upper bound for input and the entire requested output allowance. It charges the UTC month of dispatch, syncs the reservation to the locked ledger before sending and settles it from the returned usage even if the writing is rejected. A timeout or missing receipt retains the reservation. Reaching a limit, or having too little room for a request, stops the tick for both brands. Raising a limit is a local config change.

The calculations use standard, global, short-context prices per million input/output tokens of $2/$10 for Sonnet and $0.10/$0.50 for Haiku, checked November 27, 2026. Recheck `social/claude.py` when prices change. Anthropic charges incurred by other applications are outside the local ledger. [Models and IDs](https://platform.claude.com/docs/en/models/overview) · [Messages](https://platform.claude.com/docs/en/api/messages/create) · [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)

### Enable the schedule

After completing config, run these commands **when ready to start the weekly previews**. Loading the LaunchAgent starts the first cycle, which prepares the first preview. This is an example plist following the Mini’s per-user `RunAtLoad`, `StartInterval`, PATH and log pattern.

**When the inbox starts (coverage gap).** Loading the LaunchAgent does not start the inbox. A brand's inbox starts only after Hank's first approved preview that names its accounts. After that approval, each destination's first cycle only records its start time and reads nothing; replies and handoffs begin with messages and comments that arrive after that cycle (about 15 minutes after the approval). Everything that arrived before then, including all messages from before Blotato was connected, is answered by hand in Instagram, Facebook or the Blotato inbox. A destination that is held in the preview starts only after a later approved preview names it.

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

To pause the Mini, run both commands below. `disable` is what keeps it paused after a restart or a new login: without it, macOS loads the plist from `~/Library/LaunchAgents` again at login and `RunAtLoad` starts a cycle (inbox replies included). While paused nothing new reaches Blotato; only posts already submitted (each within three hours of its slot) remain in Blotato’s calendar, where they can be canceled if needed.

```sh
launchctl disable "gui/$(id -u)/com.acacia.social-autopilot"
launchctl bootout "gui/$(id -u)/com.acacia.social-autopilot"
launchctl print-disabled "gui/$(id -u)" | grep com.acacia.social-autopilot   # shows => disabled
```

To resume, enable first (a disabled job refuses to load), then load it again. Loading starts a cycle at once.

```sh
launchctl enable "gui/$(id -u)/com.acacia.social-autopilot"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.acacia.social-autopilot.plist"
launchctl print "gui/$(id -u)/com.acacia.social-autopilot"
```

Keep `~/Library/Application Support/social-autopilot/ledger/` backed up privately. Its durable write intents and provider receipts prevent duplicate submissions after a crash. A second process exits when the ledger lock is held.

## Content, media and inbox

- Claude Sonnet 5.5 composes seven complete captions per brand in one request using the launch kit’s voice and Central Time windows. It has a rotating set of 17 source cards per brand. The Mini validates each caption against that day’s complete factual wording, permitted topics and editorial phrases, and saves the draft before media uploads. Retrying an interrupted upload reuses the same captions. The Mini rechecks saved copy before opening a preview. Prices, review counts and testimonials are excluded. The booking CTA uses the collection linked in the launch kit. Each Acacia card cites a section of FACTS.md in `social/editorial.py`.
- Before a batch is scheduled, the Mini checks the pinned facts and STR brief for their respective brands. Acacia also compares the local facts and audit blobs to current upstream main and runs the source repo’s own banned-claim scanner on complete captions. If a source changes, review its diff, update affected cards and the source pins, and run the tests. Recheck pending previews.
- Stay Eden uses its property’s full-size files under `public/images` in the stays repo. For Acacia, the PM’s existing finished-job selection uses the two tags `after` and `drone`. The Mini checks exact per-photo tags, metadata, visibility and original pixels. It rejects location metadata, unfamiliar labels and descriptive metadata, then uses a local vision model to screen for faces, house numbers, street signs, readable text, plates and identifiable homes. It requires a matching scene, an empty list of observed risks and an explicit clear decision. Then the exact JPEG bytes that would be uploaded go through Apple's on-device face and person detectors (`pyobjc-framework-Vision`, installed by `requirements.txt`), independently of the model's answer: any face or person, or a detector that cannot run, holds the photo. This catches the case the model missed in review, a small framed portrait on a bedroom wall (kept as `test/fixtures/framed-portrait-room.webp`). Only re-encoded JPEG pixels go to Blotato. Before each scheduled submission that carries a photo, the Mini rechecks its source: the CompanyCam photo must still be active, public (not internal), tagged `after` and `drone`, and unchanged (same `updated_at` and file hash); the Stay Eden file must still be on upstream `main` (fetched in that cycle) with the same blob. A withdrawn or changed photo holds that day's rows with an issue; a replacement needs a new preview. If CompanyCam cannot answer, the row waits for the next cycle. A Stay Eden image missing from the repo is skipped and the next candidate is tried; a broken checkout still stops the week loudly. Automated detection can miss details, so inspect the first two weeks’ photos on the phone. If media is unavailable or uncertain, Instagram is held and eligible text destinations retain their caption. [CompanyCam photos](https://docs.companycam.com/reference/listphotos) · [Photo tags](https://docs.companycam.com/reference/listphototags) · [Blotato media uploads](https://help.blotato.com/rest-api-reference/publish-post/upload-media-v2-media.md)
- After checking account, Page, conversation, age and intent, the Mini asks Claude Haiku 5.5 to compose a reply to an eligible Stay Eden booking, availability or area question. Haiku receives only the public answer material. The Mini compares the full returned text with the permitted answers and routes a rejected answer to the host. Date requests go to the booking link. The brief gives no standard check-in time, so these questions go to the host. Compound requests, rich messages, nested comments and uncertain identity go to human review. Acacia produces suggested drafts only. Handoff issues give a channel and time to find the original privately in Blotato. Close a DM handoff after resolving it to release its thread. Every handoff stays linked to its thread: reopening one takes the thread back to the host at once, and while any handoff of a thread is open nothing in that thread gets an automatic reply. The inbox for a brand starts only after Hank approves a week whose preview named its accounts, and it pauses (with an issue) if Blotato later reports a different name or ID for one of them. Each destination then starts with new arrivals (see **When the inbox starts** above) and keeps its own read position, so an outage on one platform cannot skip that platform's messages. Blotato imports nothing from before an account connected and keeps messages for 45 days, so an empty history is not proof of a new conversation. A DM gets an automatic reply only with positive evidence: either Hank closed every handoff in that thread, or Blotato reports the conversation (`GET /v2/conversations/{id}`, `createdAt`) as started after that destination's inbox began and within the last 44 days, and its full retained history is exactly this one message. Anything else, including a read error, goes to the host.

Each automatic reply can make a Blotato active contact: someone reached through one connected account in a billing period. Above the plan's allowance, non-legacy plans bill each extra contact after the period ends (Solo: $0.02), and there is no provider-side spending cap. The `blotato` section of the local config sets an explicit ceiling. Before each reply to a person not yet reached this period, the Mini reserves `contactChargeCents` against `contactChargeLimitCents`, assuming no allowance is left, and keeps the reservation even when the send's outcome is unknown. When the next reply would go over, the message goes to the host and one issue per period says the budget is used. Setting `contactChargeLimitCents` to 0 turns every automatic reply off at once, including replies to people already reached this period. Set `billingPeriodDay` to the day the period starts in Blotato **Settings → Billing** (1 for a legacy plan or trial, which reset on the 1st). Any day from 1 to 31 works: in a shorter month the period starts on its last day (a period starting on the 31st starts on February 28, then March 31). [Billing and active contacts](https://help.blotato.com/settings/billing-and-credits#active-contacts)

| Setting | Example value | Meaning |
| --- | ---: | --- |
| `contactChargeLimitCents` | 200 | At most $2 of worst-case contact charges per billing period (100 new contacts); 0 turns every automatic reply off |
| `contactChargeCents` | 2 | Solo plan rate per extra contact; use 1 for Team |
| `billingPeriodDay` | 1 | Day of the month (1 to 31) the Blotato billing period starts (UTC); month-end rollover as above | [Comments](https://help.blotato.com/rest-api-reference/comments.md) · [Messages](https://help.blotato.com/rest-api-reference/messages.md)
- The Monday issue covers the prior Monday through Sunday in Central Time and catches up after downtime. It counts the destinations that week's preview named (or, with no preview that week, the ones Hank last confirmed), so a lookup failure on Monday does not drop a platform. It sums reported post reach, shows how many posts have a measurement, ranks the top three, and suggests the next test. Shared audiences may be counted across posts. The documented IG/FB endpoints expose post metrics without account-level follower history. Follower change is labeled unavailable. Where Blotato supplies the post-level `followsCount` metric, the digest shows that count with its coverage. Google Business Profile analytics are unavailable. [Post list and snapshots](https://help.blotato.com/rest-api-reference/publish-post/list-posts.md) · [Analytics](https://help.blotato.com/rest-api-reference/analytics.md)

## Delivery and recovery

The approval token covers the brand, week, exact captions, times, media, the photos' private source references, destination IDs and the destination names shown in the preview. The Mini checks the current private preview, the approving comment's author ID and GitHub's edit record for that comment (read through GraphQL: no edit time, no editor). A row is submitted only in the last three hours before its slot (`SUBMIT_AHEAD` in `social/workflow.py`), after the approval or `autoPublish`, the preview and the photo source are checked again. At each submission it also checks that Blotato still reports the destination (name and IDs) the preview verified; a different one holds the row with an issue, a disconnected destination waits for the next cycle, and a destination the preview marked held is never sent anything. It sends one `POST /v2/posts` per eligible destination with `scheduledTime` at the request body root. Accepted submission IDs are polled through the status endpoint. Blotato answers `in-progress` for a submission it cannot match, so polling stops a day after the slot: a post still not confirmed then is marked `unresolved` and gets one issue for manual reconciliation. Queued replies get the same deadline. If one destination is disconnected or unverified (for example Acacia's Google Business location), an issue names it and the brand's other verified destinations, the digest and delivery checks keep running. A rate-limited scheduling request retries on a later cycle. An ambiguous write stays on hold and produces an issue. Inspect the matching brand, time and destination in the Blotato calendar before completing any missing post manually. Apply the same procedure in the Blotato inbox for a held reply. The Mini never replays an ambiguous write automatically. [Publish](https://help.blotato.com/rest-api-reference/publish-post.md) · [Submission status](https://help.blotato.com/rest-api-reference/publish-post/get-post.md)
