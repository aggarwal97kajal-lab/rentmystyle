# RentMyStyle: how to run it (no coding needed)

1. Install Python 3.9 or newer from python.org (free). Nothing else to install.
2. Open a terminal / command prompt inside this folder and run:  python3 server.py   (Windows: python server.py)
3. The first run prints your ADMIN PASSWORD once. Save it.
4. Storefront: http://localhost:8000      Admin dashboard: http://localhost:8000/admin

Demo owner (for testing): owner@demo.local / demo1234. Delete or change it before going live.

## Real now
Sign-up/log-in (hashed passwords), listings, search and filters, date availability with double-booking protection,
10-minute payment hold, server-side price calculation, separate security-deposit ledger, owner delivery steps,
return, deposit refund, disputes with admin review, reviews, owner earnings, admin stats/users/listings/bookings/
disputes/reports/fees. Owner pickup addresses are shown only to the renter after payment.

## Not done yet
- Photos: listings show an illustration; photo upload is not built. Owners cannot block their own dates yet.
- Real payments are wired for Razorpay but UNTESTED against a live account. Add a payment webhook and issue refunds and owner payouts through your Razorpay dashboard (the app marks them 'refund_due').
- No photo upload, email/SMS OTP, courier integration or photo search yet.
- Going online needs hosting with HTTPS (e.g. Render or Railway). SQLite is fine to start; move to PostgreSQL as you grow.
- Have payments, KYC, GST/TCS and your rental terms reviewed by professionals before launch.

Options: PORT=8000  ADMIN_PASSWORD=your-own  RMS_DB=path/to/database.db

## Payment methods
The checkout offers UPI, cards, net banking and wallets. Card details are never typed into or stored by this app.
- Test mode (default): payments succeed instantly and no money moves. Use it to try everything.
- Live mode: create a Razorpay account (needs business KYC), then start the server with
    RAZORPAY_KEY_ID=your_key RAZORPAY_KEY_SECRET=your_secret python3 server.py
  The server creates the order, and confirms payment only after checking Razorpay's signature. Try it with Razorpay's test keys first.
