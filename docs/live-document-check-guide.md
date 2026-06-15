# Live Document Check — Setup Guide

**Live Document Check** lets a customer scan a QR code printed on a SQL Account document
(invoice, DO, etc.) and instantly view the **live PDF straight from the issuer's SQL
Account** — so they can confirm the hardcopy in their hand matches the system record. If the
document was cancelled in SQL Account, the customer sees a "cancelled" notice instead.

The QR link is **tamper-proof**: it carries an HMAC-SHA256 signature, so a customer cannot
edit the document number (or company / format) in the URL to view documents that aren't
theirs.

---

## How it works

```
SQL Account report (.fr3)                GOLINK server (print.golink.com.my)
─────────────────────────                ───────────────────────────────────
On print, a script builds a signed                 Customer scans QR
URL and renders it as a QR:                                │
  /v?c=<company>&k=<doctype>             ┌─────────────────▼──────────────────┐
     &n=<docno>&t=<format>&s=<sig>       │ 1. Re-compute signature, compare    │
            │                            │ 2. If mismatch → "Invalid link"     │
            ▼                            │ 3. Fetch live doc from SQL Account   │
     QR printed on document              │ 4. Cancelled? → show notice         │
            │                            │ 5. Else → return the live PDF        │
            └──── customer scans ───────▶└─────────────────────────────────────┘
```

- The **signature** (`s`) is `HMAC-SHA256(secret, "c|k|n|t")`, truncated to 16 hex chars.
- The **secret** is unique per company and is **never** the SQL Account API key.
- The signature is computed **inside the report** and re-checked **on the server** — both
  use the same secret, so a tampered link fails instantly.

---

## What you need from GOLINK

Each company needs a **Company ID** and a **signing secret**, both issued by GOLINK.

> 📧 To obtain them, email **info@golink.com.my** with your company details.

You will receive:
- **Company ID** — e.g. `acme-sdn-bhd`
- **Signing secret** — a 64-character string

---

## Part A — Create the Live Document Check setting in SQL Account

The report reads the Company ID and signing secret from a dedicated **Quotation** record,
so one setting serves **every** report format in that SQL Account — no need to edit the
script per company.

1. Go to **Sales → Quotation → New**.
2. Create a quotation with these exact values:
   | Field | Value |
   |---|---|
   | **Doc No** | `GOLINKLiveDocCheck` (exact, case-sensitive) |
   | **Company Name** (customer name) | your **Company ID** (from GOLINK) |
   | **Validity** | your **Signing Secret** (from GOLINK) |
3. **Save** the quotation.

> ⚠️ Do **not** delete or edit this `GOLINKLiveDocCheck` quotation later — every QR depends
> on it. Changing the Company Name or Validity will break verification for documents
> already printed.

> The signing secret sits in this quotation's Validity field — visible to your internal
> SQL Account users (who already own the data), but never exposed to customers.

---

## Part B — Set up the report format

> **Only FastReport `.fr3` formats are supported.** The legacy **RTM** engine has no
> scripting and cannot generate the signature — an RTM format must be rebuilt as `.fr3`.

> 💾 **Back up your original `.fr3` before editing.**

### Option 1 (fastest): start from the sample
Use the working sample in [`sample-templates/`](../sample-templates/) as a reference, then
adapt your own layout.

### Option 2: add it to an existing `.fr3`

**1. Add the signing functions.** In the report's **Code** tab, paste the entire
`HMAC-SHA256` block (see [Appendix](#appendix--full-report-script)) **before**
`procedure Setup;`. Keep the `//<-----HMAC-SHA256 begin/end----->` markers.

**2. Load the setting in `procedure Setup;`.** Inside `procedure Setup;`, add the query
that reads the Company ID + secret from the `GOLINKLiveDocCheck` quotation into a dataset:

```pascal
  SQL := 'SELECT CompanyName As CompanyID, Validity As SigningSecret FROM SL_QT ' +
         'WHERE DocNo=''GOLINKLiveDocCheck'' ';
  AddDataSet('plLiveDocCheck', ['CompanyID', 'SigningSecret'])
  .GetDBData(SQL);
```

The QR builder (added **after** `procedure Setup;` — see Appendix) then reads
`<plLiveDocCheck."CompanyID">` and `<plLiveDocCheck."SigningSecret">`.

**3. Add a QR barcode object.**
- Insert a **Barcode** object, set its symbology to **QR Code** (2D).
- Rename the object to **`BarcodeHMACSHA256`**.
- Make it large enough with error-correction level **M** (the URL is ~200 characters; a
  too-small QR won't scan reliably).

**4. (Optional) Add a text object** named **`MmHMACSHA256`** to display the raw URL — handy
for testing. *In production you can hide it. If you delete the object, also delete the
`MmHMACSHA256.Text := url;` line, or the script will error.*

**5. Set the document type.** In the builder procedure, set `docTypeKey` to match this
report's document type. Valid keys:

```
sales_quotation, sales_order, delivery_order, sales_invoice,
cash_sale, credit_note, debit_note
```

**6. Add the QR builder** (the `BarcodeHMACSHA256OnBeforePrint` procedure — see Appendix)
**after** `procedure Setup;`.

**7. Wire the event.** ⚠️ *Most-forgotten step:* connect `BarcodeHMACSHA256OnBeforePrint`
to the **`BarcodeHMACSHA256`** object's **OnBeforePrint** event (Object Inspector → Events).
Without this, the script never runs.

The format name is filled **automatically** from the report's own format name — no need to
type it. (It uses the report format name *without* the `.fr3` extension, which must match
the name SQL Account uses to identify the format.)

---

## Testing

1. Preview/print the document.
2. If you added the `MmHMACSHA256` text object, check the URL looks like:
   ```
   https://print.golink.com.my/v?c=acme-sdn-bhd&k=sales_invoice&n=IV-00001&t=<hex>&s=<16 hex chars>
   ```
3. Scan the QR (or open the URL):

| Result | Meaning |
|---|---|
| The live PDF opens | ✅ Working |
| "This document has been cancelled in system" | Document is cancelled in SQL Account |
| "Document not found" | No such document number at that company |
| "Invalid or expired link" | Signature/Company ID/secret mismatch — recheck the `GOLINKLiveDocCheck` quotation's Company Name / Validity |
| "Unable to display document" | Format name not found in SQL Account, or API issue |

> **IDOR check:** change the document number in the URL to another invoice → it must show
> "Invalid or expired link", never another customer's document.

> **Local testing:** set `baseUrl` to `http://<server-ip>:<port>/v`. Note `127.0.0.1` only
> works on the server PC itself — for phone scanning use the PC's LAN IP.

---

## ⚠️ Important cautions (read before going live)

The signature and the format name are **baked into each printed QR at print time**. So
changing certain things later will **break QR codes already printed and handed to
customers**:

- **Renaming the report format** — if you rename the format in SQL Account *after* QRs have
  been printed, those old printed links can no longer find the format → customers get
  **"Unable to display document"**. **Do not rename a format once its QRs are in
  circulation.**
- **Regenerating the signing secret** — invalidates **every** QR ever printed for that
  company → **"Invalid or expired link"**. Only regenerate if the secret is exposed.
- **Changing the Company ID** — same effect: all previously printed QRs break.
- **Editing or deleting the `GOLINKLiveDocCheck` quotation** — the report reads the Company
  ID and secret from it at print time, so changing its Company Name / Validity (or removing
  it) breaks new and existing QRs.

In short: **the `GOLINKLiveDocCheck` quotation (Company ID + secret) and the report format
name must stay stable** for as long as printed documents need to remain verifiable.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "Invalid or expired link" | Company ID or secret wrong/blank in the `GOLINKLiveDocCheck` quotation; or the format name changed after printing. Verify Company Name / Validity match what GOLINK issued. |
| "Unable to display document" | The format name in the QR no longer exists in SQL Account (renamed), or an API/credentials problem. |
| "Document not found" | The document number doesn't exist at that company's SQL Account. |
| QR won't scan | QR too small / low error correction — enlarge it, set error-correction **M**. |
| Nothing happens on print | The `OnBeforePrint` event isn't wired to `BarcodeHMACSHA256`. |
| Blank `c`/`s` in the URL (always "Invalid link") | The `GOLINKLiveDocCheck` quotation is missing, or the `procedure Setup;` dataset query (`plLiveDocCheck`) wasn't added. |
| Script compile error about a deleted object | You removed `MmHMACSHA256` but left the `MmHMACSHA256.Text := url;` line. |

---

## Appendix — full report script

Paste this entire block in the report's **Code** tab, **before** `procedure Setup;`.

```pascal
//<-----HMAC-SHA256 begin----->
function ToHex8(x: Int64): String;
var i, nib: Int64; hx, r: String;
begin
  hx := '0123456789abcdef'; r := '';
  for i := 7 downto 0 do
  begin
    nib := (x shr (i * 4)) and 15;
    r := r + Copy(hx, nib + 1, 1);
  end;
  Result := r;
end;

function RotR(x, n: Int64): Int64;
begin
  Result := ((x shr n) or (x shl (32 - n))) and $FFFFFFFF;
end;

function SHA256(msg: String): String;
var
  K: array[0..63] of Int64;
  HS: array[0..7] of Int64;
  W: array[0..63] of Int64;
  i, t, nBlocks, bs, j, m32: Int64;
  a, b, c, d, e, f, g, h: Int64;
  s0, s1, ch, maj, t1, t2: Int64;
  bitLen, hi, lo: Int64;
  padded, res: String;
begin
  m32 := $FFFFFFFF;
  K[0]:=$428a2f98;  K[1]:=$71374491;  K[2]:=$b5c0fbcf;  K[3]:=$e9b5dba5;
  K[4]:=$3956c25b;  K[5]:=$59f111f1;  K[6]:=$923f82a4;  K[7]:=$ab1c5ed5;
  K[8]:=$d807aa98;  K[9]:=$12835b01;  K[10]:=$243185be; K[11]:=$550c7dc3;
  K[12]:=$72be5d74; K[13]:=$80deb1fe; K[14]:=$9bdc06a7; K[15]:=$c19bf174;
  K[16]:=$e49b69c1; K[17]:=$efbe4786; K[18]:=$0fc19dc6; K[19]:=$240ca1cc;
  K[20]:=$2de92c6f; K[21]:=$4a7484aa; K[22]:=$5cb0a9dc; K[23]:=$76f988da;
  K[24]:=$983e5152; K[25]:=$a831c66d; K[26]:=$b00327c8; K[27]:=$bf597fc7;
  K[28]:=$c6e00bf3; K[29]:=$d5a79147; K[30]:=$06ca6351; K[31]:=$14292967;
  K[32]:=$27b70a85; K[33]:=$2e1b2138; K[34]:=$4d2c6dfc; K[35]:=$53380d13;
  K[36]:=$650a7354; K[37]:=$766a0abb; K[38]:=$81c2c92e; K[39]:=$92722c85;
  K[40]:=$a2bfe8a1; K[41]:=$a81a664b; K[42]:=$c24b8b70; K[43]:=$c76c51a3;
  K[44]:=$d192e819; K[45]:=$d6990624; K[46]:=$f40e3585; K[47]:=$106aa070;
  K[48]:=$19a4c116; K[49]:=$1e376c08; K[50]:=$2748774c; K[51]:=$34b0bcb5;
  K[52]:=$391c0cb3; K[53]:=$4ed8aa4a; K[54]:=$5b9cca4f; K[55]:=$682e6ff3;
  K[56]:=$748f82ee; K[57]:=$78a5636f; K[58]:=$84c87814; K[59]:=$8cc70208;
  K[60]:=$90befffa; K[61]:=$a4506ceb; K[62]:=$bef9a3f7; K[63]:=$c67178f2;

  HS[0]:=$6a09e667; HS[1]:=$bb67ae85; HS[2]:=$3c6ef372; HS[3]:=$a54ff53a;
  HS[4]:=$510e527f; HS[5]:=$9b05688c; HS[6]:=$1f83d9ab; HS[7]:=$5be0cd19;

  bitLen := Length(msg) * 8;
  padded := msg + Chr(128);
  while (Length(padded) mod 64) <> 56 do
    padded := padded + Chr(0);
  hi := (bitLen shr 32) and m32;
  lo := bitLen and m32;
  padded := padded + Chr((hi shr 24) and 255) + Chr((hi shr 16) and 255)
                   + Chr((hi shr 8) and 255)  + Chr(hi and 255)
                   + Chr((lo shr 24) and 255) + Chr((lo shr 16) and 255)
                   + Chr((lo shr 8) and 255)  + Chr(lo and 255);

  nBlocks := Length(padded) div 64;
  for i := 0 to nBlocks - 1 do
  begin
    bs := i * 64;
    for t := 0 to 15 do
    begin
      j := bs + t * 4 + 1;
      W[t] := ((Ord(padded[j]) shl 24) or (Ord(padded[j+1]) shl 16)
            or (Ord(padded[j+2]) shl 8) or Ord(padded[j+3])) and m32;
    end;
    for t := 16 to 63 do
    begin
      s0 := (RotR(W[t-15],7) xor RotR(W[t-15],18) xor (W[t-15] shr 3)) and m32;
      s1 := (RotR(W[t-2],17) xor RotR(W[t-2],19) xor (W[t-2] shr 10)) and m32;
      W[t] := (W[t-16] + s0 + W[t-7] + s1) and m32;
    end;

    a:=HS[0]; b:=HS[1]; c:=HS[2]; d:=HS[3]; e:=HS[4]; f:=HS[5]; g:=HS[6]; h:=HS[7];
    for t := 0 to 63 do
    begin
      s1 := (RotR(e,6) xor RotR(e,11) xor RotR(e,25)) and m32;
      ch := ((e and f) xor ((not e) and g)) and m32;
      t1 := (h + s1 + ch + K[t] + W[t]) and m32;
      s0 := (RotR(a,2) xor RotR(a,13) xor RotR(a,22)) and m32;
      maj:= ((a and b) xor (a and c) xor (b and c)) and m32;
      t2 := (s0 + maj) and m32;
      h:=g; g:=f; f:=e; e:=(d + t1) and m32;
      d:=c; c:=b; b:=a; a:=(t1 + t2) and m32;
    end;

    HS[0]:=(HS[0]+a) and m32; HS[1]:=(HS[1]+b) and m32;
    HS[2]:=(HS[2]+c) and m32; HS[3]:=(HS[3]+d) and m32;
    HS[4]:=(HS[4]+e) and m32; HS[5]:=(HS[5]+f) and m32;
    HS[6]:=(HS[6]+g) and m32; HS[7]:=(HS[7]+h) and m32;
  end;

  res := '';
  for i := 0 to 7 do res := res + ToHex8(HS[i]);
  Result := res;
end;

function HexToRaw(h: String): String;
var i, v1, v2, o1, o2: Int64; r, c1, c2: String;
begin
  r := '';
  i := 1;
  while i <= Length(h) - 1 do
  begin
    c1 := Copy(h, i, 1);  c2 := Copy(h, i + 1, 1);
    o1 := Ord(c1[1]);     o2 := Ord(c2[1]);
    if o1 <= 57 then v1 := o1 - 48 else v1 := o1 - 87;
    if o2 <= 57 then v2 := o2 - 48 else v2 := o2 - 87;
    r := r + Chr((v1 shl 4) or v2);
    i := i + 2;
  end;
  Result := r;
end;

function HMAC_SHA256(key, msg: String): String;
var i, o: Int64; ikey, okey, kk: String;
begin
  kk := key;
  if Length(kk) > 64 then kk := HexToRaw(SHA256(kk));
  while Length(kk) < 64 do kk := kk + Chr(0);
  ikey := '';  okey := '';
  for i := 1 to 64 do
  begin
    o := Ord(kk[i]);
    ikey := ikey + Chr((o xor $36) and 255);
    okey := okey + Chr((o xor $5c) and 255);
  end;
  Result := SHA256(okey + HexToRaw(SHA256(ikey + msg)));
end;

function HexEncode(s: String): String;
var i, o: Int64; hx, r: String;
begin
  hx := '0123456789abcdef'; r := '';
  for i := 1 to Length(s) do
  begin
    o := Ord(s[i]);
    r := r + Copy(hx, (o shr 4) + 1, 1) + Copy(hx, (o and 15) + 1, 1);
  end;
  Result := r;
end;
//<-----HMAC-SHA256 end----->
```

Inside `procedure Setup;`, load the Company ID + secret from the `GOLINKLiveDocCheck`
quotation into a dataset:

```pascal
  SQL := 'SELECT CompanyName As CompanyID, Validity As SigningSecret FROM SL_QT ' +
         'WHERE DocNo=''GOLINKLiveDocCheck'' ';
  AddDataSet('plLiveDocCheck', ['CompanyID', 'SigningSecret'])
  .GetDBData(SQL);
```

And the QR builder, placed **after** `procedure Setup;` and wired to
`BarcodeHMACSHA256.OnBeforePrint`:

```pascal
procedure BarcodeHMACSHA256OnBeforePrint(Sender: TfrxComponent);
var
  baseUrl, companyId, docTypeKey, docNo, formatName, secret, payload, sig, url: String;
begin
  baseUrl    := 'https://print.golink.com.my/v';
  companyId  := <plLiveDocCheck."CompanyID">;     // from GOLINKLiveDocCheck quotation > Company Name
  docTypeKey := 'sales_invoice';                  // must match this report's document type
  formatName := Report.ReportOptions.Name;        // this report's format name (no .fr3 extension)
  secret     := <plLiveDocCheck."SigningSecret">; // from GOLINKLiveDocCheck quotation > Validity
  docNo      := <Main."DocNo">;                   // the document number field

  // format name is part of the signed payload, so it cannot be tampered with
  payload := companyId + '|' + docTypeKey + '|' + docNo + '|' + formatName;
  sig     := Copy(HMAC_SHA256(secret, payload), 1, 16);

  url := baseUrl + '?c=' + companyId + '&k=' + docTypeKey + '&n=' + docNo +
         '&t=' + HexEncode(formatName) + '&s=' + sig;

  BarcodeHMACSHA256.Text := url;
  MmHMACSHA256.Text := url;   // optional: remove this line if you delete the text object
end;
```

---

*Live Document Check is a feature of GOLINK's SQL Account Print service. For Company IDs,
signing secrets, or support: **info@golink.com.my**.*
