# siemens-protocol

A tool to convert Siemens MR protocol PDF's — the human-readable export 
of a full exam protocol — into hierarchical JSON's describing every scan 
and every parameter, in order to do useful work (analyzing and comparing 
protocols, etc.)

The point is to make protocol rebuilds after a software upgrade faster and
less error-prone, and to leave a machine-readable record that can be diffed
across software versions.

See [Design.md](Design.md) for the design this implements.

## Install

Linux, macOS and Windows, on Python 3.10 through 3.14. The package is pure
Python and its one dependency, PyMuPDF, ships wheels for all three, so nothing
is compiled and no system package is needed. If you would rather install
nothing at all, there is a container image — see
[Running from a container](#running-from-a-container).

On Linux or macOS:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows, in PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

Three names describe this one project, and they are deliberately different:

| | Name | Where it appears |
| --- | --- | --- |
| The command | **`siemens-protocol-tool`** | What you type: `siemens-protocol-tool parse ...` |
| The distribution | `siemens-protocol` | What you install: `pip install siemens-protocol` |
| The import | `siemens_protocol` | What you import: `import siemens_protocol` |

If a command is not found, check you are typing `siemens-protocol-tool` — the
package name is not the command name.

Installing puts that executable in `.venv/bin/` (`.venv\Scripts\` on Windows),
which is only on `PATH` while the environment is activated. Activating is what
the examples below assume. To skip activation, call it by its full path instead:

```sh
.venv/bin/siemens-protocol-tool parse protocol.pdf
```

To have the command everywhere without activating anything, install it as a
standalone tool, which puts it in a directory that is already on `PATH`:

```sh
uv tool install .        # or: pipx install .
```

### The OCR extra

The OCR fallback is the only part that needs anything outside Python, so it is
an optional extra rather than a dependency. Installing it is a two-step job: the
Python binding, then the tesseract binary itself.

```sh
pip install -e ".[ocr]"
```

| Platform | Install tesseract with |
| --- | --- |
| macOS | `brew install tesseract` |
| Debian, Ubuntu | `sudo apt install tesseract-ocr` |
| Fedora, RHEL | `sudo dnf install tesseract` |
| Windows | `winget install UB-Mannheim.TesseractOCR`, or `choco install tesseract` |

You almost certainly do not need this unless you have scans of paper copies of pdf's (and you might!).
Every example file of every supported
release carries a clean native text layer, so none of them takes the OCR path;
see [Note on OCR](#note-on-ocr). Without the extra, `--ocr never` and the
default `--ocr auto` both work normally, and only `--ocr always` fails, saying
so.

The tool finds the binary on `PATH`, and failing that in the usual install
location for the platform — which is what makes a stock Windows install work,
since its installer writes to `C:\Program Files\Tesseract-OCR` and adds nothing
to `PATH`. If yours is somewhere else, name it:

```sh
siemens-protocol-tool parse protocol.pdf --ocr always --tesseract /opt/local/bin/tesseract
```

or set `SIEMENS_PROTOCOL_TESSERACT` to the same path once and leave it set.

## Running from a container

The tool is published to Docker Hub as `fredericklab/protoparser`, built for
both `linux/amd64` and `linux/arm64`. The image carries the OCR extra *and* the
tesseract binary, so the one part of this tool that needs something outside
Python is already there — see [OCR needs no setup here](#ocr-needs-no-setup-here).

```sh
docker pull fredericklab/protoparser:latest
```

That is about 480 MB to fetch and about 1.9 GB unpacked.

| Tag | What it is |
| --- | --- |
| `latest` | rebuilt from `main` or `dev` on every push |
| `latest-release` | the most recently published GitHub release |
| `0.2.0` | one release, pinned — use this when you need a reproducible result |

Only `latest` exists at the moment. The other two are pushed by the workflow
that fires when a GitHub release is published, and there has not been one yet.

### Running a command

The image sets no `ENTRYPOINT`, and its default command is `python3`, so
`docker run fredericklab/protoparser:latest` on its own drops you at a Python
prompt. Name the command you want:

```sh
docker run --rm fredericklab/protoparser:latest siemens-protocol-tool versions
```

The container starts out with none of your files. Mount the directory you are
working in at `/data` and add `-w /data`, and the paths you type are then the
same ones you would type outside:

```sh
docker run --rm -v "$PWD":/data -w /data fredericklab/protoparser:latest \
    siemens-protocol-tool list examples/XA60/R01StressDyn.pdf
```

Every subcommand works that way; only the mount is new:

```sh
# parse one protocol, writing the JSON back out to the host
docker run --rm -v "$PWD":/data -w /data fredericklab/protoparser:latest \
    siemens-protocol-tool parse examples/XA60/R01StressDyn.pdf --out R01StressDyn.json

# parse a whole tree; the output mirrors it, here as json/XA60/..., json/VE11C/...
docker run --rm -v "$PWD":/data -w /data fredericklab/protoparser:latest \
    siemens-protocol-tool parse examples --out json

# compare one protocol across two scanner software versions
docker run --rm -v "$PWD":/data -w /data fredericklab/protoparser:latest \
    siemens-protocol-tool diff examples/VE11C/R01StressDyn.pdf examples/XA60/R01StressDyn.pdf
```

Anything outside the mount is invisible to the tool, which is worth remembering
when a path that exists on your machine comes back as not found: `/data` is the
only place the container can see. That cuts both ways for output — the
directory form of `--out` creates its tree, but the single-file form does not
create the directory above it, and a mount is the only place it could write to
anyway.

### OCR needs no setup here

The two-step install described under [the OCR extra](#the-ocr-extra) — the
Python binding and then a native tesseract — is already done in the image, so
`--ocr always` runs with nothing else to install:

```sh
docker run --rm -v "$PWD":/data -w /data fredericklab/protoparser:latest \
    siemens-protocol-tool parse examples/VB17A/rtNIRS_12ch.pdf --ocr always --stdout
```

Expect it to be slow — every page is rasterized at 300 DPI and read back —
around 50 seconds for that 31-page export against under a second natively. You
still will not need it for a normal PDF export; see [Note on OCR](#note-on-ocr).

### Files it writes are owned by root

Everything in the image is installed as root and the container runs as root, so
on Linux the JSON that lands in your bind mount belongs to `root`. The reflex
fix, `--user "$(id -u):$(id -g)"`, does not work here: `uv tool install` puts
the command under `/root/.local/bin`, and `/root` is mode 0700, so a non-root
user cannot even read it.

```
/root/.local/bin/siemens-protocol-tool: [Errno 13] Permission denied
```

Take ownership afterwards instead:

```sh
sudo chown -R "$(id -u):$(id -g)" json
```

On macOS and Windows this does not come up — Docker Desktop maps ownership
through its VM and the files arrive belonging to you.

### The graphical front end from a container

Two of [the GUI's](#the-graphical-front-end) defaults have to change. It binds
`127.0.0.1`, which inside a container is the *container's* loopback and is
unreachable from your browser, so it needs `--host 0.0.0.0`; and it takes any
free port, which cannot be published because you do not know the number in
advance, so it needs a fixed `--port`:

```sh
docker run --rm -p 127.0.0.1:8080:8080 -v "$PWD":/data \
    fredericklab/protoparser:latest \
    siemens-protocol-tool gui --host 0.0.0.0 --port 8080 --dir /data
```

There is no browser in the container for it to open, so it prints the URL and
waits:

```
siemens-protocol-tool GUI serving on http://0.0.0.0:8080/?token=VWgux4jOZB6aZv1hF7tk
Press Ctrl-C to stop.
```

Open that with `localhost` in place of `0.0.0.0`, keeping the token — the
server checks it on every request and mints a fresh one each run.

Publishing as `-p 127.0.0.1:8080:8080` rather than the shorter `-p 8080:8080`
is the point of that form: `--host 0.0.0.0` has widened the bind inside the
container, and restricting the published port to the host's loopback is what
keeps a server that runs commands from being offered to the rest of the
network. `--dir` sets the directory the file picker opens in and that relative
paths resolve against, so point it at the mount.

### Building the image yourself

```sh
./builddocker.sh          # local, native architecture only
PUSH=1 ./builddocker.sh   # both architectures, pushed to Docker Hub
```

A local build cannot be multi-architecture: `--load` writes into the local
daemon, which holds one architecture per tag, so the full matrix is built only
when publishing. The script also refuses to move `:latest` unless HEAD is a
clean, tagged commit, so a test build cannot overwrite the tag other people
pull; anything else is tagged with its `git describe` string alone.

One consequence of the build worth knowing: `.git` is excluded from the build
context, so `setuptools-scm` has no tag to read inside the image and the number
must be passed in as `VERSION` (see [Versioning](#versioning)). This script and
both GitHub Actions workflows get it by running `setuptools-scm` itself rather
than by reshaping `git describe`, and that distinction is not cosmetic: only
the first is PEP 440. An invalid version does not degrade to a fallback —
`packaging` raises `InvalidVersion` inside the build backend, so the image
simply fails to build.

The tag and the version are also not the same string for an untagged build. A
Docker tag may not contain `+`, which every development version carries, so the
tag is that version with `+` swapped for `_`: `0.1.1.dev23+gabc1234` is pushed
as `0.1.1.dev23_gabc1234`. A release has no `+` and keeps its bare number.

Images built before this was sorted out report `0.0.0.dev0+unknown`, because
nothing passed `VERSION` to them. The build stamp they carry says what they
actually are:

```sh
docker run --rm fredericklab/protoparser:latest \
    bash -lc 'echo "$GITVERSION $GITSHA $BUILD_TIME"'
# v0.1.0+19.g52e11a89 52e11a8924727b12de3b119d181e281c2f27964d 2026-08-24T19:41:26+00:00
```

## Versioning

The version lives in exactly one place: the git tag. There is no number in
`pyproject.toml`, none in `__init__.py`, and no `VERSION` file, so there is
nothing to keep in step and nothing that can drift. Releasing is one command:

```sh
git tag v0.2.0
git push --tags
```

Between tags, `setuptools-scm` derives a development version from the distance
to the last one, which makes an unreleased build obvious on sight:

```sh
$ siemens-protocol-tool --version
siemens-protocol-tool 0.2.1.dev3+g908a065      # 3 commits past v0.2.0
```

The same number reaches `siemens_protocol.__version__`, `pip show`, and the
Docker image label. Note that `--version` before a subcommand reports the
tool's version, while `--release` after one forces a Siemens release profile;
these were both spelled `--version` until the tool grew a version of its own,
and the old spelling still works but is no longer advertised.

One wrinkle worth knowing: `.git` is excluded from the Docker build context,
so `setuptools-scm` cannot read a tag there. `builddocker.sh` and the two
Docker workflows work out the version before the build and pass it in as a
build argument, which the Dockerfile hands to `setuptools-scm` through
`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SIEMENS_PROTOCOL`. They ask `setuptools-scm`
for it rather than reformatting `git describe`, because only its answer is
PEP 440 — a describe string such as `0.1.0-23-gabc1234` raises `InvalidVersion`
in the build backend and fails the image build outright. A build that skips the
argument is labelled `0.0.0.dev0+unknown` rather than borrowing the last real
release number.

## Use

Everything here has a graphical equivalent — see
[the graphical front end](#the-graphical-front-end) — but the command line is
the primary interface and what the rest of this document describes.

```sh
siemens-protocol-tool parse examples/XA60/R01StressDyn.pdf
siemens-protocol-tool parse examples/ --out parsed/          # batch a directory
siemens-protocol-tool versions                               # list version profiles

# inventory one protocol: scans, sequences, times, and the total
siemens-protocol-tool list protocol.pdf

# which scans run a sequence Siemens did not supply
siemens-protocol-tool sequences protocol.pdf

# just the ones a migration has to rebuild, with the evidence
siemens-protocol-tool sequences protocol.pdf --only flagged --explain

# check a protocol against preferred values
siemens-protocol-tool check protocol.pdf

# compare two protocols scan by scan
siemens-protocol-tool diff old.pdf new.pdf

# compare one scan across two protocols
siemens-protocol-tool diff old.pdf new.pdf --scan T1_MEMPRAGE_64ch

# compare two scans within one protocol
siemens-protocol-tool diff protocol.pdf --left-scan SpinEchoFieldMap_AP --right-scan SpinEchoFieldMap_PA

# narrow a comparison to one section of the scanner's tabs
siemens-protocol-tool diff old.pdf new.pdf --filter contrast
```

| Option | Meaning |
| --- | --- |
| `--out PATH` | Write JSON here. Alongside the input as `.json` by default; a directory in batch mode. |
| `--release {auto,VB17A,VE11C,XA30,XA60}` | Force a Siemens release profile. Default `auto`. |
| `--ocr {auto,always,never}` | Control the OCR fallback. Default `auto`. |
| `--dpi N` | Rasterization DPI for OCR pages. Default 300. |
| `--tesseract PATH` | Path to the tesseract binary, when it is installed off `PATH`. Same as `SIEMENS_PROTOCOL_TESSERACT`. |
| `--no-flatten` | Omit the flattened per-scan view (included by default). |
| `--emit-debug PATH` | Dump per-span geometry for tuning a new version. |
| `--stdout` | Write JSON to stdout instead of a file (single file only). |

## The graphical front end

Everything below can also be driven from a window, for anyone who would rather
not type it:

```sh
siemens-protocol-gui                 # or: siemens-protocol-tool gui
```

That serves a page on the loopback interface and opens it in your default
browser. There is no toolkit to install and nothing extra to `pip install`:
the server is standard library, and the part that differs between Linux, macOS
and Windows is the browser, which you already have. It is the same reasoning
that keeps OCR an optional extra — a GUI toolkit would have put a
`brew install`, an `apt install` or a hundred-megabyte wheel in front of every
user.

| | |
| --- | --- |
| One tab per subcommand | `parse`, `diff`, `check`, `list`, `vocab` and `versions`, with every option the command line takes |
| A file picker that returns real paths | The server browses the filesystem, so what you pick is a path the tool can open rather than an uploaded copy |
| The command line, always visible | Every form shows the exact command it is about to run, with a button to copy it |
| Live output | Standard output and standard error, interleaved as the tool produces them, with a Stop button |

The command line shown is not a reconstruction: the form is generated from one
description of the tool's options, and that same description builds the
arguments that are actually passed. So the GUI is a way to *learn* the command
line rather than a substitute for it — set up a run in the window, copy the
line, and put it in a script.

```
$ siemens-protocol-gui
siemens-protocol-tool GUI serving on http://127.0.0.1:52413/?token=...
Press Ctrl-C to stop.
```

| Option | Meaning |
| --- | --- |
| `--port N` | Serve on this port. Default is any free one. |
| `--host ADDR` | Interface to bind. Default `127.0.0.1`, and see the warning below. |
| `--dir DIR` | Directory commands run in, which relative paths resolve against. Default the current one. |
| `--no-browser` | Print the URL instead of opening a browser. |

**On security.** The server runs commands as you and can read any file you can,
so it binds to the loopback interface and mints a random token per session.
Every request must carry that token, requests naming an unexpected `Host` are
refused, and no CORS header is ever sent — which together keep other pages in
your browser, and anything using DNS rebinding, from reaching it. The token is
in the URL that gets opened and is wiped from the address bar once the page has
it, so a copied URL or a screenshot does not hand it over. Reloading from a
bookmark therefore will not work; start the GUI again and use the URL it
prints. Do not widen `--host` unless you have thought about who is on the
network.

## Listing a protocol

`list` inventories one protocol in acquisition order — index, scan name,
sequence binary and acquisition time — and totals the scan time:

```
$ siemens-protocol-tool list examples/XA60/ELS2_20210802XA60.pdf
examples/XA60/ELS2_20210802XA60.pdf (XA60)

   #  scan                                   sequence         TA
  --  -------------------------------------  --------  ---------
   0  localizer                              fl           19 sec
   1  AAHScout                               fl           17 sec
*  2  T1_MEMPRAGE_64ch                       tfl_me     6:02 min
*  3  slice_positioning 22 degree angle CCF  epfid        10 sec
*  4  rfMRI_REST_AP_CCF                      epfid     10:10 min
*  5  rfMRI_REST_PA_CCF_distortion           epfid        18 sec
*  6  SpinEchoFieldMap AP CCF                epse          8 sec
*  7  VOC_run1_AP_CCF                        epfid      4:10 min
*  8  VOC_run2_AP_CCF                        epfid      4:10 min
*  9  VOC_run3_AP_CCF                        epfid      4:10 min
* 10  VOC_run4_AP_CCF                        epfid      4:10 min
* 11  EmotionConflict_AP_CCF                 epfid     13:24 min
  12  t2_tse_dark-fluid_tra                  tir        4:14 min
  13  pd+t2_tse_tra                          tse        2:11 min
  14  resolve_4scan_trace_tra_p2_192         resolve    1:55 min
  --  -------------------------------------  --------  ---------
      total (15 scans)                                     55:48

10 of 15 scans do not run a recognized Siemens sequence (* third-party, ? not
accounted for). Run 'sequences' for what they are.
```

The leading mark is the one thing here that is not copied off the page: `*`
means the scan runs a sequence Siemens did not supply, `?` that the tool could
not account for it either way. Ten of these fifteen scans are third-party, and
that number rather than the 55:48 is what decides how much work a release
migration is — see [third-party sequences](#third-party-sequences).

Times are shown exactly as the export prints them, which is not consistent:
VE11C writes `6:02` and `8.0 s`, the Numaris/X releases write `6:02 min` and
`9 sec`. All four are understood, and the total is normalized to `M:SS`, or
`H:MM:SS` past an hour.

A time the parser cannot read is marked `?` and left out of the total, with a
count printed underneath. It is never counted as zero — a total that quietly
omits a scan reads as though it covered everything.

`--json` emits the same data with each duration in seconds plus a
`total_seconds`, and each row's `verdict`, for scripting. The input may be a
PDF or a JSON file from `parse`, including one written with `--no-flatten`:
the mark is derived from scan headers and sections, never from the flattened
view.

## Third-party sequences

This is the reason the tool exists. Siemens' own conversion moves *stock*
sequences between releases reliably. What forces a manual rebuild — and a
side-by-side comparison of two PDFs — is a third-party sequence: CMRR's
multiband EPI, MGH's navigated MPRAGE, a site's own spectroscopy binary. The
new release either has no equivalent installed or has one whose parameters do
not line up. `sequences` says which scans those are.

```
$ siemens-protocol-tool sequences examples/XA60/ELS2_20210802XA60.pdf
examples/XA60/ELS2_20210802XA60.pdf (XA60)

10 third-party, 0 unrecognized, 5 stock, of 15 scans

third-party sequences present:
  - CMRR (University of Minnesota) -- EPI package (C2P), multiband off or not printed
  - CMRR (University of Minnesota) -- multiband EPI, BOLD
  - MGH / A. A. Martinos Center -- MEMPRAGE -- multi-echo MPRAGE

   #  scan                                   sequence  identified as
  --  -------------------------------------  --------  -------------
   0  localizer                              fl        Siemens -- FLASH -- spoiled gradient echo
   1  AAHScout                               fl        Siemens -- FLASH -- spoiled gradient echo
*  2  T1_MEMPRAGE_64ch                       tfl_me    MGH / A. A. Martinos Center -- MEMPRAGE -- multi-echo MPRAGE
*  3  slice_positioning 22 degree angle CCF  epfid     CMRR (University of Minnesota) -- multiband EPI, BOLD
*  4  rfMRI_REST_AP_CCF                      epfid     CMRR (University of Minnesota) -- multiband EPI, BOLD
   ...
  12  t2_tse_dark-fluid_tra                  tir       Siemens -- turbo inversion recovery
  13  pd+t2_tse_tra                          tse       Siemens -- turbo spin echo
  14  resolve_4scan_trace_tra_p2_192         resolve   Siemens -- RESOLVE -- readout-segmented diffusion

* rebuild and check by hand   ? not accounted for, check by hand
```

| Option | Meaning |
| --- | --- |
| `--only {third-party,unrecognized,stock,flagged}` | List only these scans. `flagged` is third-party and unrecognized together — the rebuild list. Counts always cover every scan. |
| `--explain` | Show the evidence behind each identification, and the catalog's notes. |
| `--catalog DIR` | A directory of signature catalogs overlaying the shipped one. |
| `--release {auto,VB17A,VE11C,XA30,XA60}` | Force a release profile for a PDF input. |
| `--json` | Emit the findings as JSON. |
| `--out PATH` | Write the report here instead of stdout. |

Finding third-party sequences is not an error exit. A research protocol is
expected to be full of them, and an exit code would make every ordinary run
look like a scripted failure.

### The three signals

None is always present, so all three are used, and each is sufficient alone.

**The sequence binary.** `header.sequence` names the kernel. On VB17A this is
the sequence *file* name — `cmrr_mbep2d_bold`, `mjd_mclean_flipback`,
`tfl_mgh_multiecho` — and identifies the sequence outright. On the Numaris/X
releases and VE11C it is only the kernel, so CMRR's multiband EPI and Siemens'
stock `ep2d_bold` both report `epfid` and the name decides nothing.

**The Special card.** `Sequence - Special` holds the parameters the sequence
author added, so its labels were chosen by that author rather than by Siemens,
and they are the same on every release the sequence was ported to. This is the
detector that separates the two `epfid` cases. In the 30 shipped examples,
169 scans print CMRR's `MB LeakBlock kernel` and `Online multi-band recon.`
and every one of them is `epfid` or `epse`; no stock sequence prints either.

**The stated owner.** VB17A, alone among the releases, introduces the binary
with a label naming who owns it — `SIEMENS:` for a stock sequence, `USER:` for
one built at the site — which the profile records as `sequence_owner`. That is
not an inference from a fingerprint; it is the scanner saying so. It partitions
all 110 VB17A example scans with no disagreement against the other two
detectors, so where it is present it *decides* the verdict, while a signature
still supplies the identity — `USER` says a sequence is not Siemens', not which
one it is. The later releases stop printing it, so it cannot replace the others.

The asymmetry is what forces all three: **VB17A prints no Special card at all** —
110 scans across five exports, none of them with one. (The only "Special"
string in those PDFs is `Special sat.`, a Geometry saturation parameter, which
is why the card is matched by section title and not by substring.) Meanwhile
one XA30 export prints a scan named `T1w_MEMPR_vNav` with no sequence field
that could be read at all, and only the Special card identifies it.

### Three verdicts, and why the third one exists

| Verdict | Meaning |
| --- | --- |
| `third-party` | The catalog names the sequence, and it is not Siemens'. Rebuild and check it. |
| `stock` | The export states Siemens as the owner, or the binary is a listed Siemens kernel — and either way the scan prints no sequence-specific parameters. |
| `unrecognized` | No detector could account for it, or two of them disagreed. |

`unrecognized` is deliberately not a finding of "third-party". Across the
shipped corpus, every scan with a non-empty Special card does turn out to run
a third-party sequence — but that is a fact about these 19 files, not a law.
`resolve` is a Siemens product sequence and does print a Special card on some
builds; a corpus that happens not to exercise it would let "non-empty card ⇒
third-party" look universal right up until it reported a stock sequence as
needing a rebuild. Nor is it a finding of "stock", which would be the worse
error: it would report a protocol as converting cleanly when it may not.

It means *check this by hand*, and `--explain` says what could not be accounted
for — an unlisted kernel, or a Special card no signature matched.

It is also what two disagreeing signals produce. If a scan matches a
third-party signature but the export states its owner is `SIEMENS`, neither
claim is quietly preferred: the scan is reported `unrecognized` with both
statements shown, because a contradiction is exactly the case a person should
look at. No scan in the shipped examples does this.

For the same reason the stock kernel list is conservative. Leaving a genuine
Siemens kernel off it produces an `unrecognized` verdict — a false alarm,
which costs a look — rather than a wrong `stock` one, which costs a rebuild.
As of the current catalog every scan in all 30 snapshots is accounted for:
**385 third-party, 204 stock, 0 unrecognized.**

### The catalog

`src/siemens_protocol/sequences/catalog.json` is data, like the
[vocabularies](#standard-parameter-names) and for the same reason: a wrong
entry hides real work instead of merely failing to name it. Each signature
carries a note recording the evidence behind it, printed by `--explain`.

```json
{
  "id": "cmrr-mb-epi-bold",
  "vendor": "CMRR (University of Minnesota)",
  "family": "multiband EPI, BOLD",
  "priority": 20,
  "note": "The MB parameters are CMRR's own and appear on no Siemens kernel ...",
  "match": {
    "binaries": ["cmrr_mbep2d_bold"],
    "base_binaries": ["epfid"],
    "special_all": ["MB LeakBlock kernel", "Online multi-band recon."]
  }
}
```

* `binaries` — sequence binary names that identify this sequence on their own.
* `special_all` / `special_any` — Special-card labels that must all, or any of
  which must, be present.
* `base_binaries` — kernels the *Special-card* route may apply to. This is what
  splits one fingerprint across the kernels it rides on: CMRR's multiband card
  appears on `epfid` for BOLD and `epse` for diffusion, and only the kernel
  says which. It never gates `binaries`, because a vendor's own binary name is
  a statement about the sequence rather than an inference from it.
* `priority` — breaks ties when a scan matches more than one signature. Needed
  because the number of conditions does not say which match is narrower: the
  CMRR *package* entry names six labels, the *multiband variant* names two plus
  a kernel, and the variant is the more specific answer.

`third_party_owners` and `stock_owners` map the values of the header's
`sequence_owner` field — `USER` and `SIEMENS` — onto what each means. This is
what resolves the VB17A binaries no fingerprint could: `tse_crusher`
(`Flair axial low SAR`) is labelled `USER` and `fl3d_rd` (`vessels_head`) is
labelled `SIEMENS`, and the export is a better authority on that than any list
here.

`path_markers` holds substrings that mark a scan as site-installed regardless
of any signature — Siemens writes `CustomerSeq` into the protocol path when it
knows. That is a statement rather than an inference, so it yields `third-party`
even when the catalog cannot name which sequence. None of the shipped examples
carries one, so the path is supported but unexercised by the corpus. (The
`\\USER\` prefix that *does* appear in several is the protocol tree root, not
a sequence marker — `\\USER\...\localizer` is stock, and reading it as a
signal would misreport every localizer in a research tree.)

`--catalog DIR` overlays additional JSON files onto the shipped catalog, so a
site can name the sequences only it runs without editing the installed package.
A signature whose `id` already exists replaces the shipped one; anything else
is appended. A flawed overlay entry is reported on stderr and skipped rather
than raised, so one mistake does not cost the other sixteen signatures.

### In the parsed JSON

Every scan carries a `provenance` block, so anything reading the JSON gets the
same answer as the report without re-deriving it:

```json
"provenance": {
  "verdict": "third-party",
  "vendor": "CMRR (University of Minnesota)",
  "family": "multiband EPI, BOLD",
  "signature": "cmrr-mb-epi-bold",
  "evidence": ["Special card prints MB LeakBlock kernel, Online multi-band recon."],
  "special_parameters": 20
}
```

It is recomputed on every serialization rather than cached, so a catalog
correction reaches JSON that was parsed before the correction was made.

## Output

```json
{
  "source_file": "examples/XA60/R01StressDyn.pdf",
  "software_version": "XA60",
  "detection": { "method": "header-string", "confidence": "high" },
  "scanner": "SIEMENS MAGNETOM 3.0T XR Numaris/X VA60A-0D4N",
  "page_count": 57,
  "scans": [
    {
      "index": 2,
      "name": "T1_MEMPRAGE_64ch",
      "path": "\\\\Research\\Investigators\\Belleau\\R01StressDyn\\T1_MEMPRAGE_64ch",
      "header": {
        "ta": "6:02 min",
        "coil_selection": "Manual",
        "voxel_size_mm": "1.0×1.0×1.0",
        "pat": "2",
        "rel_snr": "1.00",
        "sequence": "tfl_me"
      },
      "provenance": {
        "verdict": "third-party",
        "vendor": "MGH / A. A. Martinos Center",
        "family": "MEMPRAGE -- multi-echo MPRAGE",
        "signature": "memprage",
        "evidence": ["Special card prints Gradient moment factor, Readout trajectory"],
        "special_parameters": 5
      },
      "sections": {
        "Routine": { "Slab Group": "1", "Slices per Slab": "176" },
        "Contrast - Common": { "TR": "2530.0 ms", "TI": "1100 ms" }
      },
      "flat": {
        "TR": {
          "value": "2530.0 ms",
          "sections": ["Routine", "Contrast - Common", "Geometry - Common"],
          "conflict": false
        }
      },
      "pages": [7, 8, 9]
    }
  ]
}
```

Values are kept as raw strings. Splitting value from unit is deliberately left
to a later normalization layer, so the capture stays faithful to the printout.

### Parameters printed in several sections

Siemens prints many parameters more than once per scan — TR, FoV and slice
thickness are the usual suspects. `sections` keeps every occurrence under the
section it was printed in, so no reading is dropped.

`flat` then collapses each key to one entry and records where it came from:

* occurrences agree → `conflict: false` and the shared `value`;
* occurrences disagree → `conflict: true` and a `values` map of section to
  value, so the discrepancy is visible instead of one reading winning at
  random.

The CLI reports the conflict count per file. Those are worth reading before a
rebuild.

### Keys that repeat inside one section

A scan with three slice groups prints `Slice Group` three times, each followed
by its own indented `Slices` and `Distance Factor`. Dropping the repeats would
lose real readings, so second and later occurrences are suffixed positionally:

```json
{ "Slice Group": "1", "Slices": "5",
  "Slice Group #2": "2", "Slices #2": "1" }
```

## Checking against preferred values

`check` reports parameters that depart from a site's preferences, which is the
other half of a rebuild: the diff says what moved, this says what is wrong.

```sh
siemens-protocol-tool check protocol.pdf
siemens-protocol-tool check examples/ --quiet          # every PDF beneath a directory
siemens-protocol-tool check protocol.pdf --json
```

```
NOCICEPT_Ph2MRI515_Second.pdf (VE11C) against policy 'default'
  scan 15: dMRI_dir99_AP
    ! MB RF phase scramble = 'Off' [Sequence - Special] -- prefer 'On'
        Phase scrambling reduces peak RF amplitude in multiband excitation;
        leaving it off risks SAR limiting and slice leakage.
  22 readings checked, 2 errors, 0 warnings
```

Exit status is `1` when anything was found, `0` when nothing was; `!` marks an
error and `?` a warning, and `--warnings-ok` passes on warnings alone.

### Writing a policy

A policy is a JSON file of rules. Shipped ones live in
`src/siemens_protocol/policy/`; `--policy-dir DIR` searches your own first, and
`--policy` takes either a name or a path.

```json
{
  "name": "default",
  "rules": [
    { "parameter": "MB RF phase scramble", "section": "Sequence - Special",
      "equals": "On", "reason": "..." },
    { "parameter": "Excite pulse duration", "section": "Sequence - Special",
      "min": 3000, "unit": "us", "reason": "...", "severity": "warning" }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `parameter` | The parameter to check. Required. |
| `section` | Restrict to one section. Optional; otherwise checked wherever it appears. |
| `equals` / `one_of` / `not_equals` | Value preferences, compared case-insensitively. |
| `min` / `max` | Inclusive numeric bounds against the reading's leading number. |
| `unit` | Expected unit for a bound. A reading in another unit is reported, not compared. |
| `reason` | Why the preference exists. Shown in the report. |
| `severity` | `error` (default) or `warning`. |

Four things worth knowing:

- **A rule fires only where its parameter is present.** A localizer that never
  prints a multiband setting is not in violation of a multiband rule. Rules
  that matched nothing at all are listed separately, since a persistent one is
  usually stale.
- **One rule covers every release.** Parameters match on canonical name via the
  vocabularies, so a rule written as `PAT mode` also checks XA60's
  `Acceleration Mode`, and the report quotes whichever label that release
  actually printed.
- **Units are not assumed.** A bound of `3000 us` against a reading of `3 ms`
  is reported as a unit mismatch rather than silently passing.
- **A malformed rule fails at load.** A rule with no constraint, a non-numeric
  bound or an unknown severity is an error, not a rule that quietly never
  matches.

## Comparing protocols

`diff` answers the question a rebuild actually poses: what really changed, as
opposed to what Siemens merely renamed. It has two modes.

**Protocol against protocol.** Scans are aligned by *sequence*, not by name — a
protocol can print the same name twice (two field maps), and a release can
rename one scan while leaving its position alone. An inserted or deleted scan is
reported as such instead of shifting everything after it out of step.

**Scan against scan.** Name a scan per side with `--left-scan` and `--right-scan`.
With two files that compares one scan of each; with one file it compares two scans
of that file. The names need not match, which is the point — a scan the site or the
vendor renamed still has a counterpart. Naming only one side uses the same name on
the other. Scans are selected by name or by zero-based index.

Because scans are aligned by sequence rather than by name, a matched pair can still
be spelled differently on each side. Whenever that happens the report says so
explicitly, rather than leaving you to infer it:

```
Names do not match exactly - rfMRI_REST_ME_PA_distortion (left) corresponds to rfMRI_REST1_ME_PA_distortion (right)
```

In a whole-protocol comparison the note leads the affected scan's block; when you
named the two scans yourself it leads the report.

Naming the same file on both sides is the same request as giving it once, and costs
one parse rather than two:

```
siemens-protocol-tool diff p.pdf        --left-scan AP --right-scan PA   # identical
siemens-protocol-tool diff p.pdf p.pdf  --left-scan AP --right-scan PA   # to this
``` Comparing the two field maps
of one protocol is a good check that they differ only where they should:

```
$ siemens-protocol-tool diff R01StressDyn.pdf --left-scan SpinEchoFieldMap_AP --right-scan SpinEchoFieldMap_PA
SpinEchoFieldMap_AP -> SpinEchoFieldMap_PA
  parameters
    Sequence - Special
      ~ Invert RO/PE polarity: Off  |  On
```

### Where a difference lives

A difference is only useful if you can find the control that produces it, so
each one is listed under the section that prints it — and specifically the
section in the **right-hand** protocol, since that is the one being edited.
Sections come out in the order the right-hand file prints them, and within a
section the parameters keep their printed order too, so reading the report
top to bottom is the same walk you make through the scanner's own tabs.

A parameter the right-hand release no longer prints has no section there, so it
is filed under the one it had on the left. A section only the left-hand file
has is slotted in beside its own card rather than dumped at the end: VB17A's
single `Contrast` lands next to VE11C's `Contrast - Common`, not pages away
from it.

`--filter` narrows the report to one card. Names are the top-level section,
lower-cased — `properties`, `routine`, `contrast`, `resolution`, `geometry`,
`system`, `sequence`, and so on — so one name covers all of that card's tabs:
`contrast` brings both `Contrast - Common` and `Contrast - Dynamic`. A full
section name is accepted and folded to its card, so a name pasted out of the
report works. `header` selects the scan's header box, which is not a card but
can be asked for the same way. Give several by repeating the option or with a
comma-separated list, and a name no section matches is an error that lists the
ones these two files do have.

```
$ siemens-protocol-tool diff VE11C/R01_Mindfulness.pdf XA60/R01_Mindfulness.pdf --scan AAHScout_64ch --filter geometry
--- VE11C/R01_Mindfulness.pdf: AAHScout_64ch
+++ XA60/R01_Mindfulness.pdf: AAHScout_64ch
showing only sections: geometry

AAHScout_64ch
  parameters
    Geometry - Tim Planning Suite
      + Set-n-Go Protocol: Off
      ~ Table position -> Table Position: [H, 0 mm]  |  [0 mm, H]
      + Inline Composing: Off
  cosmetic: 1 relabeled (use --show-cosmetic to list)
```

Filtering happens *after* the two sides are paired, never before. Siemens moves
parameters between cards across releases, and restricting each side's keys
first would leave a moved parameter matched against nothing — reported as an
addition that never happened. Because the filter only ever hides a difference
that was already classified, running every section in turn reproduces the
unfiltered report exactly, each difference once. The counts it prints describe
the filtered view, which is why the report says which sections it was
restricted to.

| Option | Meaning |
| --- | --- |
| `--left-scan NAME` | Scan to take from the left input, by name or zero-based index. |
| `--right-scan NAME` | Scan to take from the right input. Omit either to reuse the other's name. |
| `--scan NAME` | Shorthand: once for both sides, twice for left then right. |
| `--filter SECTION` | Report only this top-level section. Repeatable, or comma-separated. |
| `--exact-keys` | Compare key spellings literally; do not match relabeled keys. |
| `--show-cosmetic` | List relabeled, recased and reformatted differences instead of counting them. |
| `--show-identical` | Include scans that have no differences. |
| `--json` | Emit the comparison as JSON. |
| `--out PATH` | Write the report to a file instead of stdout. |

Either input may be a PDF or JSON this tool wrote earlier, so a protocol can be
parsed once and compared many times. The exit status is `1` when any substantive
difference was found, `0` when none was, which makes it usable in a check.

### Standard parameter names

Case-folding and abbreviations only reach relabeling that is typographic.
Between releases Siemens also *reorganizes*: VE11C's `PAT mode` is XA60's
`Acceleration Mode`, its `Img. Scale Cor.` is `Image Scaling`, and its `Load
images to viewer` names a viewer that was itself renamed to MR View&GO. Nothing
in the spelling connects those.

So each release carries a JSON dictionary in
`src/siemens_protocol/vocabulary/` mapping its own labels onto shared
**canonical names**, which are snake_case so they stay distinguishable from the
space-separated forms ordinary normalization produces:

```json
{ "aliases": { "PAT mode": "acceleration_mode" } }     // VE11C.json
{ "aliases": { "Acceleration Mode": "acceleration_mode" } }   // XA60.json
```

The mapping works in both directions — `vocab list --canonical NAME` answers
what each release calls a standard parameter:

```sh
siemens-protocol-tool vocab list --canonical acceleration_mode
siemens-protocol-tool vocab list VE11C          # every mapping, with its notes
siemens-protocol-tool vocab check               # validate the dictionaries
```

A lookup that misses on the literal label is retried on its normalized form, so
one entry covers a release's own spelling variants — XA60 prints both
`Acceleration Mode` and `Accel. Mode`, and a single mapping catches each.

Point `--vocabulary DIR` at a directory of JSON files to overlay the shipped
ones without editing the installed package; `--no-vocabulary` on `diff` turns
the layer off entirely.

#### Why these are curated, not inferred

A wrong entry is worse than a missing one: it hides a real difference instead of
merely failing to explain one. Statistical pairing cannot be trusted here — any
two parameters that exist in only one release and sit in the same section
co-occur perfectly, which is how a naive pass confidently proposes
`Save uncombined` → `Radial Sorting`.

`vocab suggest LEFT RIGHT` therefore proposes candidates *with their evidence*
(support, value agreement, section agreement, and the actual values on each
side) and never applies them. Two rules held while curating the shipped data:

- **Only one-to-one renames qualify.** XA60 *merged* VE11C's `Normalize` and
  `Prescan Normalize` into one parameter, and *split* `Reference scan mode` into
  two. Neither is a rename, so neither is mapped; both stay visible as an add
  plus a remove. Declined candidates are recorded in each file's `rejected`
  block with the reason, so nobody re-adds them later.
- **Mappings are verified against real exports.** `vocab check --against A.pdf
  B.pdf` catches a mapping that *steals* a match — one where the other release
  still prints the source label natively. That is exactly what a split parameter
  looks like, and it is invisible to a check of the dictionaries alone.

### Cosmetic versus substantive

Markers in the report: `~` changed, `-` only on the left, `+` only on the right,
`R` relabeled, `c` recased, `f` reformatted. The first three are substantive and
are listed in full; the last three are cosmetic and are summarized unless you
pass `--show-cosmetic`.

Keys are matched after folding case, punctuation and a **short table of
confirmed abbreviations** (`Dist.`→`Distance`, `Accel.`→`Acceleration`,
`comp.`→`Compensation`, `Corr.`→`Correction`, `enc.`→`encoding`,
`Ref.`→`Reference`, `suppr.`→`Suppression`). Values are compared likewise:
`Single shot` versus `Single Shot` is recased, `1` versus `1.00` is reformatted.

Only a bare token is expanded, never a prefix, which is what keeps `comp.` from
touching `Inline Composing` or `Compensate T2 Decay`. Expanding an abbreviation
also covers a label's numbered variants for free — `Flow comp. 1` pairs with
`Flow Compensation 1` — where a vocabulary entry would need one line per
spelling. That is the test for which mechanism a difference belongs in: a pure
abbreviation goes in the table, a genuine rename goes in a vocabulary.

Two rules keep this honest, and both matter more than they look:

- **Nothing is silently merged.** A relabeled key is reported with *both*
  spellings, and if its value also changed it stays substantive. `Distortion
  Corr.: Off` versus `Distortion Correction: 3D` is a real change that happens
  to be wearing a new name.
- **Only provable equivalences are folded.** The table is deliberately short
  rather than a similarity threshold. `Fat sat. mode` and `Fast Mode` are one
  letter apart and are entirely different parameters; a fuzzy matcher would pair
  them and invent a value change. Semantic renames like `Coil Select Mode` →
  `Coil Selection` are left as an add plus a remove for you to judge, because
  the tool cannot know they are the same thing.

Use `--exact-keys` to switch normalization off entirely and see the raw picture.
On the matched VE11C/XA60 examples that roughly doubles the reported count, which
is a fair measure of how much of a cross-version diff is pure relabeling.

A parameter that repeats within a scan (`Slice Group`, `Slice Group #2`) is
compared as a *group* rather than position by position, because the two releases
do not always print such a group in the same order and pairing `#2` against `#2`
would invent misleading matches.

## How it works

PDF in, JSON out, in five stages (`pipeline.py`):

1. **Load and inventory** (`extract/native.py`) — open with PyMuPDF, pull
   spans with geometry and font, compute a printable-character ratio per page.
2. **Text acquisition** — keep the native spans, or, for a page whose ratio is
   too low, rasterize and OCR it (`extract/ocr.py`). Both produce the same
   `Span` type, so nothing downstream knows or cares which path a page took.
3. **Layout reconstruction** (`layout/`) — split each page into its two
   columns, group spans into rows, pair labels with values, detect section
   titles, and emit an ordered `(section, key, value)` stream.
4. **Scan splitting** (`split.py`) — find the bordered header box that opens
   each scan and cut the page stream on it.
5. **Assembly** (`model.py`, `flatten.py`, `sequences/`) — build the scans,
   attach header metadata, identify what sequence each one runs, compute the
   flattened view, serialize.

Two layers sit on top of that output. `policy/` checks a protocol against
preferred values. For comparison, `diff.py` classifies differences,
`vocabulary/` maps each release's labels onto standard names, `vocabsuggest.py`
proposes and verifies those mappings, and `report.py` renders the result.

### Things that are easy to get wrong

These are the parts worth knowing about before changing a threshold.

**Header-box detection anchors on the summary line, not the path.** Read as
one wide line, an ordinary body row can look path-shaped — `1st Signal/Mode`
next to `None` reads as a slash-separated path. A candidate is accepted only
when the row carries at least two of the profile's header fields, which no
parameter row does.

**A long path wraps mid-word.** The formatter breaks
`.../slice_positioning-Angle to ACPC li` + `ne` with no separator, so the box
lines are concatenated without one. Words *within* a line are still
space-joined, which is what OCR needs.

**Rows group by vertical overlap, not by matching top edges.** An OCR'd hyphen
sits mid-line, so its box starts several points below the letters beside it;
a top-edge test splits `Contrast - Common` into two rows.

**Wrapped labels and wrapped values are merged by row pitch.** A continuation
line shares a table cell with the line above and is set tighter than the
column's normal pitch, which is what distinguishes
`Start measurement without further` + `preparation` from a new entry.

**Section titles are found by outdent, not by boldness.** Titles hang about
2.4pt left of the parameter labels. That offset is measured against rows that
certainly are parameters — the ones with a value beside them — so a column
containing no title cannot produce a false one. Font weight and the 10pt/8pt
size step are used as well, but only on native pages: OCR supplies neither,
and its row gaps scatter too widely to separate anything.

## Version flexibility

Everything version-specific lives in a `VersionProfile` (`profiles/`), which
declares only what differs: how to recognize the release, the grammar of the
header summary line, any header field recovered from a parameter instead, and
layout thresholds worth nudging. Profiles self-register; auto-detection scores
each against the first pages and `--release` always overrides.

The releases differ mainly in that header grammar:

```
VB17A  TA: 1:08   PAT: Off   Voxel size: 2.2×1.1×10.0 mm   Rel. SNR: 1.00   SIEMENS: gre
VE11C  TA: 6:02 PM: REF Voxel size: 1.0×1.0×1.0 mmPAT: 2 Rel. SNR: 1.00 : tfl_me
XA30   TA: 9 sec Coil Selection: Auto Voxel Size: 1.2×1.2×5.0 mm³ Acc:: None Rel. SNR: 1.00
XA60   TA: 6:02 min Coil Selection: Manual Voxel Size: 1.0×1.0×1.0 mm³ Acc:: 2 Rel. SNR: 1.00
```

Note `mmPAT:` with no space and `Acc::` with two colons. Rather than one
brittle regex per release, a profile lists its field labels in order and the
parser takes the text between each label and the next — spacing quirks stop
mattering. The Numaris/X releases omit the sequence binary from the box, so
their profiles recover it from the `Sequence Name` parameter via
`param_fallbacks`.

Spectroscopy prints a *volume of interest* where imaging prints a voxel size,
in every release:

```
VE11C  TA: 0:36 PM: REF VoI: 25 ×25 ×40 mmRel. SNR: 1.00 : svs_se
XA60   TA: 12 sec Coil Selection: Manual VoI: 25×25×22 mm³ Rel. SNR: 1.00
```

It is kept as its own `voi_mm` field rather than folded into
`voxel_size_mm`: a 25×25×22 mm acquisition volume and a 1×1×1 mm imaging
resolution are different quantities, and merging them would report a changed
voxel size whenever the acquisition type changed.

**Every label a release prints must be declared.** Because each field takes
the text running to the *next declared label*, an undeclared one is absorbed
by the field before it, along with everything after it. Undeclared `VoI:` did
not produce an empty field — it silently swallowed the SNR and the sequence
binary into `pm`. Two tests guard the class: no parsed header value may
contain a stray `:`, and every scan must report either a voxel size or a VoI.

XA30 and XA60 are both Numaris/X and share that grammar *verbatim*, so it is
declared once in `profiles/numaris_x.py` and each release module adds only its
version discriminator. They still differ in parameter vocabulary, which is
expressed in `vocabulary/*.json` rather than in the profile.

VB17A, the oldest release, has no `PM:` field, puts `PAT:` before the voxel size, and
introduces the sequence with a label naming its provenance — `SIEMENS:` for a stock
sequence, `USER:` for one built at the site. That provenance is kept as
`sequence_owner`, since it is the one place an export says whether a sequence is the
vendor's. It also lays its left column out differently and separates groups of
parameters with a drawn rule of dashes, so it carries its own `value_x_ratio`.

**Discriminators must be exact.** XA60 originally required `VA\d\d`, which also
matches `VA30A-03GR`; every XA30 export therefore detected as XA60 at *high*
confidence. Nothing failed loudly — the grammars are identical — but the
reported version was wrong and the wrong vocabulary was selected. Every profile
that scores at all is a detection candidate, so match the exact release number.

### Adding a release

0. Check what the release actually prints before writing anything: the header
   grammar, the column geometry, and where the contents page sits. VB17A differed
   in all three, and none of it was guessable from the other releases.
1. Copy `profiles/xa30.py`, give it a name and `require`/`reject` patterns. Add a
   rejection to `profiles/ve11c.py` as well — VE11C prints no version string, so it
   matches anything that merely names a MAGNETOM scanner. If
   it shares an existing family's header grammar, import that family's labels;
   otherwise declare its own `header_labels`.
2. Import it in `profiles/__init__.py`.
3. Add `vocabulary/<VERSION>.json`, even if it only repeats a sibling release —
   a test asserts every registered profile ships one.
4. Drop example PDFs in `examples/<VERSION>/` — the folder name is the
   ground-truth label the tests use.
5. Add hand-checked scan counts to `tests/test_scans.py` and generate snapshots
   with `SIEMENS_PROTOCOL_REGEN=1`.
4. Run `siemens-protocol-tool parse FILE --emit-debug geometry.json` and check the
   reported `value_x`, `row_pitch` and column bounds against the file. Adjust
   `LayoutConfig` on the profile only if they are off.

Core extraction, layout, splitting and output stay untouched.

## Tests

```sh
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest
```

The suite checks that auto-detection matches every example's folder name, that
scan counts and names match hand-checked expectations, that known values —
including ones printed in several sections — come through exactly, that
conflict detection fires on a constructed disagreement, and that forced OCR
recovers the same section structure as native text on the same page.

Two are worth calling out:

* **Token conservation** (`test_pipeline.py`) asserts that every body token on
  every page reappears in some key, value or section title. A mis-tuned
  threshold that drops readings cannot pass it quietly.
* **Golden snapshots** (`tests/golden/`) make a layout or profile change show
  up as a reviewable diff. Regenerate deliberately:

  ```sh
  SIEMENS_PROTOCOL_REGEN=1 .venv/bin/python -m pytest tests/test_golden.py
  ```

  Snapshots omit the flattened view, which is a pure function of `sections`
  and is tested directly, so the diffs stay about what changed.
* **Portability** (`test_portability.py`) covers the three things that differ
  by platform, without needing the platform: where the tesseract binary is
  found, what a redirected stdout can encode, and path separators anywhere a
  path is written into a file rather than merely used. Each drives the
  platform-dependent code through the seam the real platform would.

* **Sequence identification** (`test_sequences.py`) asserts the catalog's
  claims against every stored snapshot rather than against a frozen list, so
  a new example folder tightens the tests instead of editing them. The two
  that matter most are opposites: no scan printing a Special card is ever
  called `stock`, and no bare `epfid`/`epse` scan — a stock `ep2d_bold` — is
  ever claimed by CMRR. It also fails if a shipped signature matches nothing
  in the examples, since a signature no example exercises is one nothing
  verifies.

* **The browser front end** (`test_frontend.py`) executes `app.js` itself. It
  runs under `node:vm` against a small DOM in `tests/frontend/`, wired to a
  real server on a free port, and is driven the way a person drives it:
  clicking tabs, typing in fields, walking the file picker, pressing Run. What
  the page renders is compared against what the server sent it rather than
  against expectations written into the test, so adding a release or an example
  folder does not touch it. There is nothing to install — the tests skip
  without `node`, and CI fails if they do.

CI runs the suite on Linux, macOS and Windows against Python 3.10 and 3.14,
with tesseract installed on all three so the OCR fallback is exercised
everywhere rather than only on a developer's machine, and with the front-end
tests asserted to have run rather than skipped.

## Note on OCR

The design anticipated that XA60 exports render in a scrambled CID font and
would need OCR throughout. All nineteen example files — every release, header
boxes included — carry a clean native text layer at a printable ratio of 1.0,
so none of them takes the OCR path, and all values are exact. The fallback is
built, tested and wired to the printable-ratio check, and `--ocr always`
exercises it; it simply is not needed by these files.

Expect degraded fidelity when it does fire: 8pt raster text mis-reads
characters (`Auto`→`Auio`) and loses spacing (`A >> P`→`A>>P`). Scan splitting
survives, but names do not always: on an XA30 file under forced OCR, 12 of 14
scan names come back exact and 2 are lost because tesseract fails to read the
protocol path at all. Section *sets* also drift under OCR on every release,
since title detection leans on geometry the raster round trip perturbs.

## Future work

* A numeric normalization layer splitting value and unit (`{"value": 2530.0,
  "unit": "ms"}`), kept separate from the raw string capture.
* Extending the abbreviation table and the per-release vocabularies as new
  releases arrive. Confirm each entry against a matched pair and run
  `vocab check --against` before committing it, since a wrong entry hides a
  real difference.
* Conditional policy rules, so a preference can apply only to certain sequence
  types rather than wherever the parameter appears.
* Value vocabularies. Renaming reaches parameter labels but not their values:
  VE11C's `Confirm freq. adjustment: Off` is XA60's `Confirm Frequency: Never`,
  and `Coil Select Mode`'s values were recoded wholesale. Those currently
  report as changed, which is honest but noisy.
* Per-version fixtures and profiles as new releases arrive.
