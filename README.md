# Tracking Bot

Reads the vendor's tracking number of orders the Order bot bought and sends it to the web app, where the
Orders page shows it in the **Tracking** column with a **Replace** button for each number.

For each purchased order (Amazon AU for now) the bot:

1. opens the order in the purchase account (`/gp/your-account/order-details?orderID=503-…`),
2. clicks **Track package** (every package of the order, one after the other),
3. reads the **Tracking ID** and the carrier ("Delivery By Amazon", "Shipped with Australia Post"),
4. checks that the name under **Shipping Address** is the customer's name (a different name is flagged on the
   Orders page).

Nothing is bought or changed on Amazon: the bot only opens order and tracking pages.

## Upload to the marketplace (Kogan, Big W)

After reading the tracking, the bot uploads it to the marketplace order of a Kogan or Big W (MyDeal) store, in
the store's own Chrome profile, and then searches the order again to check it is there. The marketplace order
ID is the one from the marketplace (Kogan `2QK9BJGC`, Big W `1033075200`), never the Amazon order number.

* **Big W** (portal.woolworthsmarketplus.com.au/orders): search the Order No, **Add Tracking**, type the
  tracking number, pick the Courier, **Save**. Check: search again, the Shipping Status shows
  `Courier: … No: <tracking>`.
* **Kogan** (dispatch.aws.kgn.io/manage): type the OrderID next to **Search Orders**, press it, wrench icon,
  **Dispatch / Tracking**, pick the Carrier, type the tracking number, **Confirm Dispatch**. Check: search again,
  the row shows the Tracking Number and Carrier Code.

Not uploaded:

* Amazon's own numbers that start with **TBA** (e.g. `TBAU317388173`).
* A package whose ship-to name is not the customer's (check it first, then upload by hand).
* An order that already shows another tracking number on the marketplace: it is never overwritten.
* An order with several packages: only the first number is uploaded (the others are listed as skipped).

Each order's result is written in the `upload_status` column of `tracking-done.xlsx` (`verified`, `already`,
`not_verified`, `skipped`, `failed`, `dry_run`) with the reason in `upload_note`, and on the window:

```
---- upload 1 of 2: Kogan order 2QK9BJGC <- 36QT4180587301000931505 (AUP)
  2QK9BJGC 36QT4180587301000931505 -> UPLOAD VERIFIED Kogan shows 36QT4180587301000931505, carrier AUP
```

When an upload fails, a screenshot of the page is kept in `work\<job>\output\upload-<marketplace>-<order>.png`.

**Sign in once per store.** The profile is `chrome-profiles\<store name>` (the store's name in the web app):

```powershell
.\open_profile.bat "Kogan-TFS" kogan
.\open_profile.bat "My Big W store" bigw
```

Sign in to the seller portal, tick "Remember me", close the window.

**Couriers.** The carrier read on Amazon is turned into the marketplace's courier with the `couriers` table in
`tracking.json` (built-in: Australia Post → `AUP` on Kogan, `Australia Post` on Big W, and the other common
carriers by name). An option is matched by its text or its value. A carrier with no entry is marked `failed`
("add it to couriers"); an entry that is not in the marketplace's list is marked `failed` with the list's
options in the note, so the right name can be put in:

```json
"couriers": {"kogan": {"Australia Post": "AUP", "StarTrack": "STE"},
             "bigw":  {"Australia Post": "Australia Post", "Couriers Please": "CouriersPlease"}}
```

**Settings** in `tracking.json`: `"upload_tracking": false` turns the uploads off; `"upload_dry_run": true`
fills the form but presses Cancel instead of Save / Confirm Dispatch (use it for the first run).

## When it runs

* Every 3 hours the web app queues a tracking job for each auto-buy store, for orders bought in the last
  30 days that have an Amazon order number and no tracking yet (orders not shipped yet, or with more packages
  to come, are checked again next time).
* **Get tracking** on the Orders page: the whole store (button next to Recheck all) or one order (in the
  Tracking column).
* A number typed with **Replace** (or **Add**) is kept: the bot never changes an order that has a number you
  entered. The old number stays in the order's history.

## Set up on the PC

The Tracking Bot is stand-alone: the PC needs no Order bot. It needs Python with Playwright and openpyxl,
Google Chrome, and SoftEther VPN Client with the **AU-OLD** connection (same as the Order bot laptop).

```powershell
git clone https://github.com/NavrozSalim/Tracking-Bot.git C:\TrackingBot
cd C:\TrackingBot
pip install -r requirements.txt
copy tracking.example.json tracking.json
notepad tracking.json
```

In `tracking.json` set:

* `server`: the web app address (the same `server` as in the Order bot's `agent.json`).
* `token`: a web-app ingest token with the **order-bot** scope (the Order bot's token works, or make a new one).
* `vpn_account` / `vpn_ip`: AU-OLD and 103.100.38.134, the same as the Order bot (already filled in).
* `profiles` (optional): use another Chrome folder for an account, e.g.
  `{"Amazon AU": {"user_data_dir": "C:\\Chrome\\Amazon", "profile_directory": "Default"}}`.

Sign in to the Amazon purchase account once. The name is the purchase account's name on the store's Auto
Purchasing step; the profile is `C:\TrackingBot\chrome-profiles\<name>`:

```powershell
.\open_profile.bat "Amazon AU"
```

Sign in to Amazon only, tick "Keep me signed in", close the window. Then start the agent and keep its window
open:

```powershell
.\run_tracking.bat
```

**Order IDs come from the system:** each job carries the purchased orders and their Amazon order ID
(`503-…`) saved when the Order bot bought them; the Tracking Bot only opens those orders.

**VPN:** when it starts, the agent connects the SoftEther VPN account (AU-OLD) and waits for its public IP
(103.100.38.134), like the Order bot. Before a job it checks the IP and connects again if it dropped; a job
never opens Amazon from another IP, it is stopped instead.

The web app does not give the Tracking Bot a job on a purchase account while the Order bot is buying on it (and
the other way round), so the account is never used by both at once.

## The window

Every step is shown and saved in `logs\agent-<date>.log` (14 days):

```
tracking 9f1…: Kogan-TFS / Amazon AU (GET TRACKING)
   | ---- order 1 of 3: TFS-KAZ-VZHBJB0FHP-NEW (vendor order 503-2272946-3274202)
   |     order 503-2272946-3274202: 1 Track package button(s)
   |     order 503-2272946-3274202: package 1 tracking TBAU316654872, carrier Amazon, ship to Elena Legkova
==== tracking 9f1… FINISHED (exit code 0: finished) ====
```

Exit codes: 0 finished, 1 stopped (Amazon asks to sign in: run `open_profile.bat "Amazon AU"` and sign in; or
the VPN is off), 2 file refused, 3 Chrome did not start, 4 unexpected error.

## Tests

```powershell
python -m pytest -q
```

`tests/test_real_amazon.py` runs the bot in a real Chrome against local copies of Amazon's order and Track
package pages; `tests/test_marketplace.py` does the same for the Big W and Kogan upload and check steps.
