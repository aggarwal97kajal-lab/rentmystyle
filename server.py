"""RentMyStyle backend. Python 3 standard library only: no installs needed. Run: python3 server.py"""
import sqlite3, json, os, secrets, hashlib, hmac, re, base64, urllib.request, datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB = os.environ.get("RMS_DB", "rentmystyle.db")
PORT = int(os.environ.get("PORT", "8000"))
HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.environ.get("UPLOAD_DIR", os.path.join(HERE, "uploads")); os.makedirs(UPLOADS, exist_ok=True)
MAX_BODY = 6_000_000; FAILS = {}
HOLD_MIN = 10
METHODS = {"upi", "card", "netbanking", "wallet"}
RZP_ID, RZP_SECRET = os.environ.get("RAZORPAY_KEY_ID"), os.environ.get("RAZORPAY_KEY_SECRET")
MODE = "razorpay" if RZP_ID and RZP_SECRET else "test"
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, phone TEXT, pw TEXT, salt TEXT,
  role TEXT DEFAULT 'member', city TEXT, verification TEXT DEFAULT 'unverified', blocked INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS listings(id INTEGER PRIMARY KEY, owner_id INTEGER, title TEXT, description TEXT, category TEXT, brand TEXT,
  size TEXT, color TEXT, occasion TEXT, condition TEXT, purchase_paise INTEGER, rental_paise INTEGER, deposit_paise INTEGER,
  city TEXT, public_area TEXT, pickup_address TEXT, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, listing_id INTEGER, renter_id INTEGER, owner_id INTEGER, start_date TEXT, end_date TEXT,
  rental_paise INTEGER, deposit_paise INTEGER, delivery_paise INTEGER, platform_fee_paise INTEGER, total_paise INTEGER,
  status TEXT DEFAULT 'pending_payment', hold_expires_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, booking_id INTEGER, type TEXT, amount_paise INTEGER, status TEXT, provider TEXT, provider_ref TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, booking_id INTEGER, account TEXT, amount_paise INTEGER, reason TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS disputes(id INTEGER PRIMARY KEY, booking_id INTEGER, type TEXT, raised_by INTEGER, claimed_paise INTEGER, note TEXT, status TEXT DEFAULT 'open', resolution_paise INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, booking_id INTEGER UNIQUE, reviewer_id INTEGER, rating INTEGER, comment TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, reporter_id INTEGER, listing_id INTEGER, reason TEXT, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS fees(id INTEGER PRIMARY KEY, commission_bps INTEGER, delivery_paise INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY, listing_id INTEGER, filename TEXT, position INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, booking_id INTEGER, sender_id INTEGER, body TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""
FLOW = ["confirmed", "preparing", "dispatched", "delivered", "return_pickup", "returned"]

def db():
    c = sqlite3.connect(DB, isolation_level=None); c.row_factory = sqlite3.Row
    return c

def hashpw(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
def now(): return dt.datetime.utcnow().isoformat(timespec="seconds")

def init():
    c = db(); c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM fees").fetchone(): c.execute("INSERT INTO fees(commission_bps,delivery_paise) VALUES(1500,19900)")
    if not c.execute("SELECT 1 FROM users").fetchone():
        pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
        def mk(n, e, role, p):
            s = secrets.token_hex(8); c.execute("INSERT INTO users(name,email,pw,salt,role,city,verification) VALUES(?,?,?,?,?,?,'id_verified')", (n, e, hashpw(p, s), s, role, "Delhi"))
        mk("Admin", "admin@rentmystyle.local", "admin", pw)
        if os.environ.get("RMS_NO_DEMO"): print(f"\nFirst run. Admin login  ->  admin@rentmystyle.local  /  {pw}\n"); return
        mk("Demo Owner", "owner@demo.local", "member", "demo1234")
        demo = [("Maroon Embroidered Lehenga","Lehenga","Wedding",1499,25000,5000,"Delhi"),("Ivory Kanjivaram Saree","Saree","Reception",899,14000,3000,"Mumbai"),
          ("Emerald Gown","Dress","Party",1199,16000,3500,"Bengaluru"),("Gold Zari Sherwani","Sherwani","Wedding",1799,30000,6000,"Delhi"),
          ("Kundan Choker Set","Jewellery","Wedding",699,9000,2500,"Hyderabad"),("Gold Beaded Clutch","Bag","Reception",399,4500,1000,"Pune")]
        for t, cat, occ, r, b, d, city in demo:
            c.execute("INSERT INTO listings(owner_id,title,category,occasion,condition,rental_paise,purchase_paise,deposit_paise,city,public_area,pickup_address) VALUES(2,?,?,?,?,?,?,?,?,?,?)",
                      (t, cat, occ, "Excellent", r*100, b*100, d*100, city, "Central "+city, "PRIVATE: 12 Example Street, "+city))
        print(f"\nFirst run. Admin login  ->  admin@rentmystyle.local  /  {pw}\n(save this password; it is shown only once)\n")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, obj=None, ctype="application/json"):
        body = json.dumps(obj).encode() if ctype == "application/json" else obj
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers(); self.wfile.write(body)
    def user(self, c):
        t = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.blocked=0", (t,)).fetchone()
        return r
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: raise ValueError("That file is too large.")
        try: return json.loads(self.rfile.read(n) or b"{}")
        except Exception: return {}
    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def route(self, m):
        u = urlparse(self.path); p = u.path; q = {k: v[0] for k, v in parse_qs(u.query).items()}
        mu = re.fullmatch(r"/uploads/([a-f0-9]{32}\.(jpg|png|webp))", p)
        if m == "GET" and mu:
            fp = os.path.join(UPLOADS, mu[1])
            if not os.path.exists(fp): return self.send(404, b"Not found", "text/plain")
            self.send_response(200); d = open(fp, "rb").read()
            self.send_header("Content-Type", {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[mu[2]]); self.send_header("Content-Length", str(len(d)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable"); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers(); return self.wfile.write(d)
        if m == "GET" and not p.startswith("/api"):
            f = {"/": "static/index.html", "/admin": "static/admin.html"}.get(p)
            if not f: return self.send(404, b"Not found", "text/plain")
            return self.send(200, open(os.path.join(HERE, f), "rb").read(), "text/html; charset=utf-8")
        c = db()
        try:
            r = self.api(c, m, p, q); self.send(200, r)
        except PermissionError as e: self.send(403, {"error": str(e) or "Not allowed"})
        except ValueError as e: self.send(400, {"error": str(e)})
        except LookupError as e: self.send(404, {"error": str(e) or "Not found"})
        except sqlite3.IntegrityError: self.send(400, {"error": "That already exists."})
        finally: c.close()

    def api(self, c, m, p, q):
        me = self.user(c); b = self.body() if m == "POST" else {}
        need = lambda: me or (_ for _ in ()).throw(PermissionError("Please log in."))
        adm = lambda: (need()["role"] == "admin") or (_ for _ in ()).throw(PermissionError("Admin only."))
        if p == "/api/signup" and m == "POST":
            e, pw = (b.get("email") or "").strip().lower(), b.get("password") or ""
            if not re.match(r"[^@\s]+@[^@\s]+\.\w+$", e) or len(pw) < 8: raise ValueError("Enter a valid email and a password of 8+ characters.")
            s = secrets.token_hex(8)
            c.execute("INSERT INTO users(name,email,phone,pw,salt,city) VALUES(?,?,?,?,?,?)", (b.get("name", ""), e, b.get("phone"), hashpw(pw, s), s, b.get("city")))
            return self.login(c, e, pw)
        if p == "/api/login" and m == "POST": return self.login(c, (b.get("email") or "").lower(), b.get("password") or "")
        if p == "/api/config":
            f = c.execute("SELECT * FROM fees ORDER BY id DESC").fetchone()
            return {"mode": MODE, "key_id": RZP_ID if MODE == "razorpay" else None, "delivery_paise": f["delivery_paise"], "methods": sorted(METHODS)}
        if p == "/api/me": return {k: need()[k] for k in ("id", "name", "email", "role", "city", "verification")}
        if p == "/api/listings" and m == "GET":
            sql, a = "SELECT * FROM listings WHERE status='active'", []
            for k in ("city", "category", "occasion"):
                if q.get(k): sql += f" AND {k}=?"; a.append(q[k])
            if q.get("start") and q.get("end"):
                sql += " AND id NOT IN (SELECT listing_id FROM bookings WHERE status!='cancelled' AND NOT(status='pending_payment' AND hold_expires_at<?) AND start_date<=? AND end_date>=?)"
                a += [now(), q["end"], q["start"]]
            return [self.pub(x) for x in c.execute(sql + " ORDER BY id DESC LIMIT 100", a)]
        m1 = re.fullmatch(r"/api/listings/(\d+)", p)
        if m1 and m == "GET":
            x = c.execute("SELECT * FROM listings WHERE id=?", (m1[1],)).fetchone() or (_ for _ in ()).throw(LookupError())
            d = self.pub(x); d["booked"] = [[r[0], r[1]] for r in c.execute("SELECT start_date,end_date FROM bookings WHERE listing_id=? AND status!='cancelled' AND NOT(status='pending_payment' AND hold_expires_at<?)", (x["id"], now()))]
            return d
        if p == "/api/listings" and m == "POST":
            need()
            for k in ("title", "category", "city", "rental_price", "deposit"):
                if not b.get(k): raise ValueError(f"Missing {k}.")
            cur = c.execute("INSERT INTO listings(owner_id,title,description,category,brand,size,color,occasion,condition,purchase_paise,rental_paise,deposit_paise,city,public_area,pickup_address) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (me["id"], b["title"], b.get("description"), b["category"], b.get("brand"), b.get("size"), b.get("color"), b.get("occasion"), b.get("condition", "Good"),
                 int(float(b.get("purchase_price", 0)) * 100), int(float(b["rental_price"]) * 100), int(float(b["deposit"]) * 100), b["city"], b.get("area"), b.get("pickup_address")))
            return {"id": cur.lastrowid}
        mp = re.fullmatch(r"/api/listings/(\d+)/photos", p)
        if mp and m == "POST":
            need(); L = c.execute("SELECT owner_id FROM listings WHERE id=?", (mp[1],)).fetchone() or (_ for _ in ()).throw(LookupError())
            if L[0] != me["id"]: raise PermissionError("Only the owner can add photos.")
            n = c.execute("SELECT COUNT(*) FROM photos WHERE listing_id=?", (mp[1],)).fetchone()[0]
            if n >= 8: raise ValueError("You can add up to 8 photos.")
            raw = (b.get("data") or "").split(",", 1)[-1]
            try: img = base64.b64decode(raw, validate=True)
            except Exception: raise ValueError("That file could not be read.")
            if len(img) > 3_000_000: raise ValueError("Each photo must be under 3 MB.")
            ext = "jpg" if img[:3] == b"\xff\xd8\xff" else "png" if img[:8] == b"\x89PNG\r\n\x1a\n" else "webp" if img[:4] == b"RIFF" and img[8:12] == b"WEBP" else None
            if not ext: raise ValueError("Please upload a JPG, PNG or WebP photo.")
            fn = secrets.token_hex(16) + "." + ext; open(os.path.join(UPLOADS, fn), "wb").write(img)
            c.execute("INSERT INTO photos(listing_id,filename,position) VALUES(?,?,?)", (mp[1], fn, n)); return {"url": "/uploads/" + fn}
        if p == "/api/bookings" and m == "POST": return self.create_booking(c, need(), b)
        if p == "/api/bookings" and m == "GET":
            need(); return [dict(r) for r in c.execute("SELECT b.*, l.title, l.category FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE b.renter_id=? OR b.owner_id=? ORDER BY b.id DESC", (me["id"], me["id"]))]
        m2 = re.fullmatch(r"/api/bookings/(\d+)/(\w+)", p)
        if m2 and m == "POST": return self.booking_action(c, need(), int(m2[1]), m2[2], b)
        m3 = re.fullmatch(r"/api/bookings/(\d+)", p)
        if m3:
            need(); bk = self.get_bk(c, m3[1], me); d = dict(bk)
            if bk["renter_id"] == me["id"] and bk["status"] != "pending_payment":
                d["pickup_address"] = c.execute("SELECT pickup_address FROM listings WHERE id=?", (bk["listing_id"],)).fetchone()[0]
            return d
        if p == "/api/earnings":
            need(); g = lambda a: c.execute("SELECT COALESCE(SUM(l.amount_paise),0) FROM ledger l JOIN bookings b ON b.id=l.booking_id WHERE b.owner_id=? AND l.account=?", (me["id"], a)).fetchone()[0]
            return {"pending_paise": g("owner_payable"), "available_paise": g("owner_available")}
        if p == "/api/reports" and m == "POST":
            need(); c.execute("INSERT INTO reports(reporter_id,listing_id,reason) VALUES(?,?,?)", (me["id"], b.get("listing_id"), b.get("reason"))); return {"ok": True}
        # ---- admin ----
        if p.startswith("/api/admin"):
            adm(); a = p[len("/api/admin/"):]
            if a == "stats":
                one = lambda s: c.execute(s).fetchone()[0]
                tot = one("SELECT COUNT(*) FROM bookings WHERE status NOT IN ('pending_payment','cancelled')")
                return {"users": one("SELECT COUNT(*) FROM users"), "active_listings": one("SELECT COUNT(*) FROM listings WHERE status='active'"), "rentals": tot,
                  "revenue_paise": one("SELECT COALESCE(SUM(amount_paise),0) FROM ledger WHERE account='platform_revenue'"),
                  "gmv_paise": one("SELECT COALESCE(SUM(rental_paise),0) FROM bookings WHERE status NOT IN ('pending_payment','cancelled')"),
                  "avg_rental_paise": one("SELECT COALESCE(AVG(rental_paise),0) FROM bookings WHERE status NOT IN ('pending_payment','cancelled')"),
                  "cancellation_pct": round(100 * one("SELECT COUNT(*) FROM bookings WHERE status='cancelled'") / max(1, one("SELECT COUNT(*) FROM bookings")), 1),
                  "dispute_pct": round(100 * one("SELECT COUNT(*) FROM disputes") / max(1, tot), 1)}
            if a in ("users", "listings", "bookings", "disputes", "reports", "reviews", "payments"):
                cols = "id,name,email,role,city,verification,blocked,created_at" if a == "users" else "*"
                return [dict(r) for r in c.execute(f"SELECT {cols} FROM {a} ORDER BY id DESC LIMIT 200")]
            if a == "fees" and m == "GET": return dict(c.execute("SELECT * FROM fees ORDER BY id DESC").fetchone())
            if a == "fees":
                bps = int(b["commission_bps"]); dl = int(float(b["delivery_rupees"]) * 100)
                if not 0 <= bps <= 5000: raise ValueError("Commission must be 0-50%.")
                c.execute("INSERT INTO fees(commission_bps,delivery_paise) VALUES(?,?)", (bps, dl)); return {"ok": True}
            m4 = re.fullmatch(r"(users|listings)/(\d+)/(\w+)", a)
            if m4:
                col, val = {"block": ("blocked", 1), "unblock": ("blocked", 0), "verify": ("verification", "id_verified"), "remove": ("status", "removed"), "restore": ("status", "active")}[m4[3]]
                c.execute(f"UPDATE {m4[1]} SET {col}=? WHERE id=?", (val, m4[2])); return {"ok": True}
            m5 = re.fullmatch(r"disputes/(\d+)/resolve", a)
            if m5: return self.resolve(c, int(m5[1]), int(float(b.get("resolution_rupees", 0)) * 100))
        raise LookupError("No such endpoint.")

    def login(self, c, e, pw):
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip(); t = dt.datetime.utcnow().timestamp()
        FAILS[ip] = [x for x in FAILS.get(ip, []) if t - x < 900]
        if len(FAILS[ip]) >= 10: raise PermissionError("Too many attempts. Please wait 15 minutes and try again.")
        u = c.execute("SELECT * FROM users WHERE email=?", (e,)).fetchone()
        if not u or not secrets.compare_digest(u["pw"], hashpw(pw, u["salt"])) or u["blocked"]: FAILS[ip].append(t); raise PermissionError("Wrong email or password.")
        t = secrets.token_urlsafe(32); c.execute("INSERT INTO sessions(token,user_id) VALUES(?,?)", (t, u["id"])); return {"token": t, "role": u["role"], "name": u["name"]}
    def pub(self, x):  # public view: never includes the pickup address
        d = dict(x); d.pop("pickup_address", None)
        r = db().execute("SELECT AVG(v.rating), COUNT(DISTINCT b.id) FROM bookings b LEFT JOIN reviews v ON v.booking_id=b.id WHERE b.listing_id=? AND b.status='completed'", (x["id"],)).fetchone()
        d["photos"] = ["/uploads/" + x[0] for x in db().execute("SELECT filename FROM photos WHERE listing_id=? ORDER BY position", (x["id"],))]
        d["rating"] = round(r[0], 1) if r[0] else None; d["rentals"] = r[1]; return d
    def get_bk(self, c, i, me):
        bk = c.execute("SELECT * FROM bookings WHERE id=?", (i,)).fetchone() or (_ for _ in ()).throw(LookupError())
        if me["id"] not in (bk["renter_id"], bk["owner_id"]) and me["role"] != "admin": raise PermissionError()
        return bk
    def create_booking(self, c, me, b):
        try: s, e = dt.date.fromisoformat(b["start"]), dt.date.fromisoformat(b["end"])
        except Exception: raise ValueError("Choose valid pickup and return dates.")
        if e < s or s < dt.date.today(): raise ValueError("Dates must be today or later, and return must not be before pickup.")
        days = (e - s).days + 1
        c.execute("BEGIN IMMEDIATE")   # lock so two people cannot grab the same dates
        try:
            L = c.execute("SELECT * FROM listings WHERE id=? AND status='active'", (b.get("listing_id"),)).fetchone() or (_ for _ in ()).throw(LookupError("Listing not found."))
            if L["owner_id"] == me["id"]: raise ValueError("You cannot rent your own item.")
            if c.execute("SELECT 1 FROM bookings WHERE listing_id=? AND status!='cancelled' AND NOT(status='pending_payment' AND hold_expires_at<?) AND start_date<=? AND end_date>=?", (L["id"], now(), e.isoformat(), s.isoformat())).fetchone():
                raise ValueError("Those dates are no longer available.")
            f = c.execute("SELECT * FROM fees ORDER BY id DESC").fetchone()
            rent = days * L["rental_paise"]; fee = rent * f["commission_bps"] // 10000; dl = f["delivery_paise"]
            hold = (dt.datetime.utcnow() + dt.timedelta(minutes=HOLD_MIN)).isoformat(timespec="seconds")
            cur = c.execute("INSERT INTO bookings(listing_id,renter_id,owner_id,start_date,end_date,rental_paise,deposit_paise,delivery_paise,platform_fee_paise,total_paise,hold_expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (L["id"], me["id"], L["owner_id"], s.isoformat(), e.isoformat(), rent, L["deposit_paise"], dl, fee, rent + L["deposit_paise"] + dl, hold))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        return {"booking_id": cur.lastrowid, "days": days, "rental_paise": rent, "deposit_paise": L["deposit_paise"], "delivery_paise": dl, "total_paise": rent + L["deposit_paise"] + dl, "hold_expires_at": hold}
    def booking_action(self, c, me, i, act, b):
        bk = self.get_bk(c, i, me); st = bk["status"]; owner = me["id"] == bk["owner_id"] or me["role"] == "admin"
        led = lambda acct, amt, why: c.execute("INSERT INTO ledger(booking_id,account,amount_paise,reason) VALUES(?,?,?,?)", (i, acct, amt, why))
        if act == "pay":   # Amounts always come from the booking, never from the browser.
            if st != "pending_payment" or bk["hold_expires_at"] < now(): raise ValueError("This booking hold has expired. Please book again.")
            method = b.get("method")
            if method not in METHODS: raise ValueError("Choose a payment method.")
            if MODE == "test": return self.capture(c, bk, method, "test_" + secrets.token_hex(6))
            o = self.rzp("/orders", {"amount": bk["total_paise"], "currency": "INR", "receipt": f"bk{i}"})
            c.execute("INSERT INTO payments(booking_id,type,amount_paise,status,provider,provider_ref) VALUES(?,?,?,?,?,?)", (i, "order:" + method, bk["total_paise"], "created", "razorpay", o["id"]))
            return {"order_id": o["id"], "key_id": RZP_ID, "amount": bk["total_paise"]}
        if act == "verify" and MODE == "razorpay":   # called after checkout succeeds; checks the provider's signature
            oid, pid, sig = b.get("order_id", ""), b.get("payment_id", ""), b.get("signature", "")
            if not c.execute("SELECT 1 FROM payments WHERE booking_id=? AND provider_ref=?", (i, oid)).fetchone(): raise ValueError("Unknown order.")
            good = hmac.new(RZP_SECRET.encode(), f"{oid}|{pid}".encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(good, sig): raise ValueError("Payment could not be verified.")
            if st != "pending_payment": return {"status": st}
            return self.capture(c, bk, "razorpay", pid)
        if act == "advance":
            if not owner: raise PermissionError("Only the owner can update delivery steps.")
            if st not in FLOW[:-1]: raise ValueError("Cannot advance from " + st)
            n = FLOW[FLOW.index(st) + 1]; c.execute("UPDATE bookings SET status=? WHERE id=?", (n, i)); return {"status": n}
        if act == "complete":   # after the inspection window, no open dispute -> release funds
            if not owner: raise PermissionError()
            if st != "returned": raise ValueError("Item must be marked returned first.")
            if c.execute("SELECT 1 FROM disputes WHERE booking_id=? AND status!='resolved'", (i,)).fetchone(): raise ValueError("An open dispute blocks completion.")
            led("deposit_escrow", -bk["deposit_paise"], "deposit refunded to renter"); c.execute("INSERT INTO payments(booking_id,type,amount_paise,status,provider) VALUES(?,'deposit_refund',?,?,?)", (i, bk["deposit_paise"], "captured" if MODE == "test" else "refund_due", MODE))
            share = bk["rental_paise"] - bk["platform_fee_paise"]; led("owner_payable", -share, "moved to available"); led("owner_available", share, "released to owner")
            c.execute("UPDATE bookings SET status='completed' WHERE id=?", (i,)); return {"status": "completed"}
        if act == "cancel":
            if st not in ("pending_payment",): raise ValueError("Paid bookings are cancelled through support so refunds are reviewed.")
            c.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (i,)); return {"status": "cancelled"}
        if act == "dispute":
            if st not in ("returned", "delivered", "return_pickup"): raise ValueError("Disputes open after delivery.")
            c.execute("INSERT INTO disputes(booking_id,type,raised_by,claimed_paise,note) VALUES(?,?,?,?,?)", (i, b.get("type", "damage"), me["id"], int(float(b.get("claimed_rupees", 0)) * 100), b.get("note")))
            c.execute("UPDATE bookings SET status='disputed' WHERE id=?", (i,)); return {"status": "disputed"}
        if act == "review":
            if st != "completed" or me["id"] != bk["renter_id"]: raise ValueError("Only the renter can review a completed rental.")
            r = int(b.get("rating", 0))
            if not 1 <= r <= 5: raise ValueError("Rating must be 1-5.")
            c.execute("INSERT INTO reviews(booking_id,reviewer_id,rating,comment) VALUES(?,?,?,?)", (i, me["id"], r, b.get("comment"))); return {"ok": True}
        if act == "message":
            c.execute("INSERT INTO messages(booking_id,sender_id,body) VALUES(?,?,?)", (i, me["id"], (b.get("body") or "")[:1000])); return {"ok": True}
        raise LookupError("Unknown action.")
    def rzp(self, path, data):
        req = urllib.request.Request("https://api.razorpay.com/v1" + path, json.dumps(data).encode(), {"Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(f"{RZP_ID}:{RZP_SECRET}".encode()).decode()})
        try:
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception: raise ValueError("Payment provider is unavailable. Please try again.")
    def capture(self, c, bk, method, ref):
        i = bk["id"]; led = lambda a, m, w: c.execute("INSERT INTO ledger(booking_id,account,amount_paise,reason) VALUES(?,?,?,?)", (i, a, m, w))
        for t, amt in (("rental+delivery", bk["rental_paise"] + bk["delivery_paise"]), ("deposit", bk["deposit_paise"])):
            c.execute("INSERT INTO payments(booking_id,type,amount_paise,status,provider,provider_ref) VALUES(?,?,?,'captured',?,?)", (i, t + ":" + method, amt, MODE, ref))
        led("deposit_escrow", bk["deposit_paise"], "deposit held"); led("platform_revenue", bk["platform_fee_paise"], "commission")
        led("owner_payable", bk["rental_paise"] - bk["platform_fee_paise"], "owner share"); led("delivery_fees", bk["delivery_paise"], "delivery")
        c.execute("UPDATE bookings SET status='confirmed' WHERE id=?", (i,)); return {"status": "confirmed", "mode": MODE}
    def resolve(self, c, did, pay):   # admin decides how much of the escrowed deposit goes to the owner
        d = c.execute("SELECT * FROM disputes WHERE id=? AND status!='resolved'", (did,)).fetchone() or (_ for _ in ()).throw(LookupError("Dispute not found or already resolved."))
        bk = c.execute("SELECT * FROM bookings WHERE id=?", (d["booking_id"],)).fetchone()
        if not 0 <= pay <= bk["deposit_paise"]: raise ValueError("Resolution must be between 0 and the deposit.")
        L = lambda a, m, w: c.execute("INSERT INTO ledger(booking_id,account,amount_paise,reason) VALUES(?,?,?,?)", (bk["id"], a, m, w))
        L("deposit_escrow", -bk["deposit_paise"], "dispute resolved"); L("owner_available", pay, "damage settlement from deposit")
        c.execute("UPDATE disputes SET status='resolved',resolution_paise=? WHERE id=?", (pay, did))
        share = bk["rental_paise"] - bk["platform_fee_paise"]; L("owner_payable", -share, "moved to available"); L("owner_available", share, "released to owner")
        c.execute("INSERT INTO payments(booking_id,type,amount_paise,status,provider) VALUES(?,'deposit_refund',?,?,?)", (bk["id"], bk["deposit_paise"] - pay, "captured" if MODE == "test" else "refund_due", MODE))
        c.execute("UPDATE bookings SET status='completed' WHERE id=?", (bk["id"],)); return {"to_owner_paise": pay, "refunded_paise": bk["deposit_paise"] - pay}

if __name__ == "__main__":
    init(); print(f"RentMyStyle running -> http://localhost:{PORT}   Admin -> http://localhost:{PORT}/admin")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
