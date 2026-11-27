# Social autopilot

The Mini prepares a seven-day feed calendar for Stay Eden (Instagram and Facebook) and Acacia Roofing (Instagram, Facebook and Google Business Profile). Each brand has its own approval history and an `autoPublish` switch that ships set to `false`.

## On the phone

Open the private GitHub issue titled **Social · [brand] · approve [Monday]**. Read the seven captions and the channel-specific ending. Tap the photo links. Copy the exact `/approve …` line into a new comment while signed into Hank’s GitHub account. The Mini checks the issue every 15 minutes. Closing an issue holds any remaining submissions. Previously scheduled posts can be changed or canceled in Blotato’s calendar.

Sunday’s cycle prepares Monday through Sunday in Central Time. An initial midweek start prepares the next full week. Past slots expire. The second approval leaves manual approval enabled. To enable automatic scheduling for a brand, set only its `autoPublish` to `true` in the local config. It takes effect for later weeks after Hank has approved two distinct earlier weeks for that brand. A missed slot or a delivery hold stays visible in the delivery ledger and preserves the approval. Set it back to `false` to require a fresh comment for subsequent batches.

## Mini setup

1. Create or sign into the Blotato account that will hold both brands. Under **Accounts → Connect account**, connect each professional Instagram account with its own Instagram login. Connect Facebook using a login with admin access to each intended Page, select both Pages, and keep the authorizing login connected. Connect Acacia’s Google Business Profile and select its location. Enable comments, DMs and analytics for the relevant accounts. Confirm that Stay Eden’s first Instagram profile link goes to its stay collection. [Connect Instagram](https://help.blotato.com/settings/social-accounts/instagram.md) · [Connect Facebook](https://help.blotato.com/settings/social-accounts/facebook.md) · [Destinations and subaccounts](https://help.blotato.com/rest-api-reference/accounts.md)
2. In the Mini user's **Keychain Access**, add three generic password items to the login keychain. Use the exact service names below and paste each value into the password field. Grant the logged-in user’s `security` command access when prompted.
   - `social-autopilot/BLOTATO_API_KEY` holds the complete key from Blotato **Settings → API**. [Blotato key setup](https://help.blotato.com/settings/api-keys)
   - `social-autopilot/COMPANYCAM_API_KEY` holds a read-only CompanyCam key with access to photos and tags. For these first two services only, the matching environment variable takes precedence.
   - `social-autopilot/ANTHROPIC_API_KEY` holds a standard workspace API key from the Claude Console. The runtime reads this item directly from Keychain only when drafting. Use a dedicated workspace if you also want to reconcile this job with the Console's spend view. [Anthropic authentication](https://platform.claude.com/docs/en/api/overview)
3. Use Python 3.11+, Node 22+, Git and GitHub CLI. Set the paths in the example config to the original STR brief and the Acacia and Stay Eden checkouts. Refresh their local refs with `git -C ~/acacia-roofing-systems fetch origin main` and `git -C ~/str/abenteuer-stays fetch origin main`. Install the local photo inspector as described below.
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

The `claude` section of the local config sets a combined ceiling for both brands. An older config needs this section copied from the example. Existing batch approvals and submission receipts keep their original content and hashes.

| Setting | Example value | Scope |
| --- | ---: | --- |
| `maxTokensPerRun` | 16000 | Input and output tokens together, across one 15-minute tick |
| `monthlySpendLimitCents` | 1000 | $10 of this Mini's Claude usage per UTC calendar month |

Setting either value to zero pauses the entire tick. Before each Messages call the Mini reserves a conservative upper bound for input and the entire requested output allowance. It syncs the reservation to the locked ledger before sending and settles it from the returned usage even if the writing is rejected. A timeout or missing receipt retains the reservation. Reaching a limit, or having too little room for a request, stops the tick for both brands. Raising a limit is a local config change.

The calculations use standard, global, short-context prices per million input/output tokens of $2/$10 for Sonnet and $0.10/$0.50 for Haiku, checked November 27, 2026. Recheck `social/claude.py` when prices change. Anthropic charges incurred by other applications are outside the local ledger. [Models and IDs](https://platform.claude.com/docs/en/models/overview) · [Messages](https://platform.claude.com/docs/en/api/messages/create) · [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)

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

- Claude Sonnet 5.5 composes seven complete captions per brand in one request using the launch kit’s voice and Central Time windows. It has a rotating set of 17 source cards per brand. The Mini validates each caption against that day’s complete factual wording, permitted topics and editorial phrases, and saves the draft before media uploads. Retrying an interrupted upload reuses the same captions. The Mini rechecks saved copy before opening a preview. Prices, review counts and testimonials are excluded. The booking CTA uses the collection linked in the launch kit. Each Acacia card cites a section of FACTS.md in `social/editorial.py`.
- Before a batch is scheduled, the Mini checks the pinned facts and STR brief for their respective brands. Acacia also compares the local facts and audit blobs to current upstream main and runs the source repo’s own banned-claim scanner on complete captions. If a source changes, review its diff, update affected cards and the source pins, and run the tests. Recheck pending previews.
- Stay Eden uses its property’s full-size files under `public/images` in the stays repo. For Acacia, the PM’s existing finished-job selection uses the two tags `after` and `drone`. The Mini checks exact per-photo tags, metadata, visibility and original pixels. It rejects location metadata, unfamiliar labels and descriptive metadata, then uses a local vision model to screen for faces, house numbers, street signs, readable text, plates and identifiable homes. It requires a clear subject and an explicit all-clear for every flag. Only re-encoded JPEG pixels go to Blotato. Automated detection can miss details, so inspect the first two weeks’ photos on the phone. If media is unavailable or uncertain, Instagram is held and eligible text destinations retain their caption. [CompanyCam photos](https://docs.companycam.com/reference/listphotos) · [Photo tags](https://docs.companycam.com/reference/listphototags) · [Blotato media uploads](https://help.blotato.com/rest-api-reference/publish-post/upload-media-v2-media.md)
- After checking account, Page, conversation, age and intent, the Mini asks Claude Haiku 5.5 to compose a reply to an eligible Stay Eden booking, availability or area question. Haiku receives only the public answer material. The Mini compares the full returned text with the permitted answers and routes a rejected answer to the host. Date requests go to the booking link. The brief gives no standard check-in time, so these questions go to the host. Compound requests, rich messages, nested comments and uncertain identity go to human review. Acacia produces suggested drafts only. Handoff issues give a channel and time to find the original privately in Blotato. Close a DM handoff after resolving it to release its thread. The inbox starts with new arrivals on activation. [Comments](https://help.blotato.com/rest-api-reference/comments.md) · [Messages](https://help.blotato.com/rest-api-reference/messages.md)
- The Monday issue covers the prior Monday through Sunday in Central Time and catches up after downtime. It sums reported post reach, shows how many posts have a measurement, ranks the top three, and suggests the next test. Shared audiences may be counted across posts. The documented IG/FB endpoints expose post metrics without account-level follower history. Follower change is labeled unavailable. Where Blotato supplies the post-level `followsCount` metric, the digest shows that count with its coverage. Google Business Profile analytics are unavailable. [Post list and snapshots](https://help.blotato.com/rest-api-reference/publish-post/list-posts.md) · [Analytics](https://help.blotato.com/rest-api-reference/analytics.md)

## Delivery and recovery

The approval token covers the brand, week, exact captions, times, media and destination IDs. The Mini checks the current private preview and the approving comment’s immutable author ID. It sends one `POST /v2/posts` per eligible destination with `scheduledTime` at the request body root. Accepted submission IDs are polled through the status endpoint. A rate-limited scheduling request retries on a later cycle. An ambiguous write stays on hold and produces an issue. Inspect the matching brand, time and destination in the Blotato calendar before completing any missing post manually. Apply the same procedure in the Blotato inbox for a held reply. The Mini never replays an ambiguous write automatically. [Publish](https://help.blotato.com/rest-api-reference/publish-post.md) · [Submission status](https://help.blotato.com/rest-api-reference/publish-post/get-post.md)
