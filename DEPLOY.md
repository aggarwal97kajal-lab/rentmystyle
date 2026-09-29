# Putting RentMyStyle online (no coding needed)

Goal: a real web address (https://yourname.com) that anyone can open. Plan on about 45 minutes.
Estimated cost with Render: about $7/month for the server plus about $0.25 per GB/month for the storage disk. Prices change, so confirm on render.com/pricing.

## Before you start
- Run it on your own computer first (see README.md) and click through everything.
- Decide your business name and payment account. Payments need a Razorpay (or similar) account with business KYC. Do this in parallel; approval can take days.

## Step 1: Put the files on GitHub (free)
1. Create a free account at github.com.
2. Click "New repository", name it rentmystyle, choose Private, create it.
3. Click "uploading an existing file". Unzip rentmystyle-app.zip on your computer, open the rms folder and drag its CONTENTS (server.py, static folder, README.md, DEPLOY.md, render.yaml, requirements.txt) into the page.
   Do NOT upload any .db file or uploads folder from your own computer.
4. Click "Commit changes".

## Step 2: Create the server on Render
1. Create an account at render.com and connect your GitHub.
2. Click New, then Web Service, pick your rentmystyle repository.
3. Settings: Runtime Python. Build command: pip install -r requirements.txt. Start command: python3 server.py. Instance type: Free.
4. Do NOT add a Disk on the Free plan.
5. Open Environment and add these variables:
   - RMS_DB = ./rentmystyle.db
   - UPLOAD_DIR = ./uploads
   - RMS_NO_DEMO = 1   (skips the fake demo owner and listings)
   - ADMIN_PASSWORD = choose a long password of your own
6. Click Create Web Service. When it says Live, open the address Render gives you (it already has https).
7. Go to /admin on that address and log in with admin@rentmystyle.local and your ADMIN_PASSWORD.
   (Alternative: Render can read render.yaml from your repository through "New > Blueprint".)

## Step 3: Your own domain
1. Buy a domain from any registrar (GoDaddy, Namecheap, Cloudflare and others).
2. In Render, open your service, Settings, Custom Domains, add it, and copy the DNS records Render shows into your registrar. HTTPS is issued automatically.

## Step 4: Turn on real payments
1. In Razorpay, complete business KYC and get your Key ID and Key Secret.
2. First use the TEST keys. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in Render, redeploy, and make a test booking. Check the payment in the Razorpay dashboard.
3. Only after that, swap in the LIVE keys.
4. Refunds and owner payouts are still done by you in the Razorpay dashboard. The admin page shows what is due.

## Step 5: Before the public sees it
- Add a Terms, Privacy Policy and Refund/Damage policy page. Have a lawyer review them, along with GST/TCS handling.
- Test on a real phone: sign up, list an item with photos, book it from a second account, pay, complete the return.
- Back up the database: Render's disk holds rentmystyle.db and the photos. Download a copy regularly (Render Shell or a scheduled job) and consider moving to PostgreSQL when you have real volume.
- Never share your admin password or Razorpay secret. If either leaks, change it and redeploy.

## Known limits of this version
- One server, SQLite database. Good for a launch and early traffic, not for very large scale.
- No email or SMS OTP, courier integration, or automatic refunds and payouts yet.
- Owners cannot block their own dates yet, and photo AI search is not built.


## Important for the Free Render version
The Free plan has an ephemeral filesystem. The SQLite database and uploaded photos can be lost when the service redeploys, restarts, or spins down. This version is for testing/demo use. For a real marketplace, move the database to Render Postgres and file uploads to durable object storage, or use a paid service with persistent storage.
