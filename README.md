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

## When it runs

* Every 3 hours the web app queues a tracking job for each auto-buy store, for orders bought in the last
  30 days that have an Amazon order number and no tracking yet (orders not shipped yet, or with more packages
  to come, are checked again next time).
* **Get tracking** on the Orders page: the whole store (button next to Recheck all) or one order (in the
  Tracking column).
* A number typed with **Replace** (or **Add**) is kept: the bot never changes an order that has a number you
  entered. The old number stays in the order's history.

## Set up on the laptop

The Tracking Bot uses the Order bot on the same laptop: its Chrome profiles (the Amazon account is signed in
there) and its `agent.json` (server, token and VPN). Install the Order bot first.

**VPN:** like the Order bot, the agent connects the same SoftEther VPN account with the same public IP
(`vpn_account` / `vpn_ip` from the Order bot's `agent.json`: AU-OLD, 103.100.38.134) when it starts, and
connects it again before a job if it dropped. A job never opens Amazon from another IP; it is stopped instead.
If the Order bot agent already connected it, nothing changes.

```powershell
cd C:\
git clone https://github.com/NavrozSalim/Tracking-Bot.git C:\TrackingBot
cd C:\TrackingBot
copy tracking.example.json tracking.json
.\run_tracking.bat
```

`tracking.json`: `order_bot_dir` is the Order bot folder (default `C:\OrderBot`), `poll_seconds` how often it
asks for a job.

Keep the **Tracking Bot agent** window open next to the **Order bot agent** window. Both can run at the same
time: the web app never gives the Tracking Bot a job while the Order bot is using the same purchase account's
Chrome profile (Chrome allows one window per profile), and the other way round.

## The window

Every step is shown and saved in `logs\agent-<date>.log` (14 days):

```
tracking 9f1…: Kogan-TFS / Amazon AU (GET TRACKING)
   | ---- order 1 of 3: TFS-KAZ-VZHBJB0FHP-NEW (vendor order 503-2272946-3274202)
   |     order 503-2272946-3274202: 1 Track package button(s)
   |     order 503-2272946-3274202: package 1 tracking TBAU316654872, carrier Amazon, ship to Elena Legkova
==== tracking 9f1… FINISHED (exit code 0: finished) ====
```

Exit codes: 0 finished, 1 stopped (Amazon asks to sign in: open the profile with the Order bot's
`open_profile.py` and sign in), 2 file refused, 3 Chrome did not start, 4 unexpected error.

## Tests

```powershell
python -m pytest -q
```

`tests/test_real_amazon.py` runs the bot in a real Chrome against local copies of Amazon's order and Track
package pages.
