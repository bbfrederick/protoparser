# Background for processing Siemens protocols

For the project's goals, supported workflows and current limitations, start with
the [README](README.md#goals-and-current-capabilities).

## Stored parameters and UI controls

ASCCONV is the authoritative stored representation used by the sequence. It
contains both UI-controlled settings and important variables calculated by the
sequence. PDF exports and the archive's Preview section describe displayed
controls and help establish which ASCCONV representations users may edit.
The scanner may regenerate Preview when it loads a protocol, so changing only
Preview does not establish a persistent scan-parameter change.

The supported writer changes only characterized UI-to-ASCCONV representations.
It preserves unmapped and calculated variables and refuses changes requiring
calculated slice-position updates. Characterizing a mapping does not replace
the sequence's parameter-range and dependency checks on the scanner.

## Protocol organization

* When a subject is scanned in an MR scanner, they undergo a group of different measurements.
* Each measurement is optimized to measure one or more aspects of anatomy, physiology, function, or metabolism.
* A single measurement is referred to as a "scan".
* Usually, in a given MR session, the subject undergoes several scans.
* A group of scans is referred to as a "protocol".
* Siemens MR scanners group related protocols in a hierarchy, and there are two parallel naming schemes for it: Siemens' own, and this center's.  The two are synonyms level for level, and command line options accept either spelling where they name a level.

  | Level | Siemens | Local | Contains |
  |---|---|---|---|
  | lowest | scan | scan | a single acquisition with one set of parameters |
  | 2 | Program | protocol | a group of scans |
  | 3 | Exam | investigator | a group of programs/protocols |
  | top | Region | folder | a group of exams/investigators |

  At our center, we use the "Exam" level to group protocols by "investigator" (a single researcher who manages multiple experiments).  The "Region" level is used to separate research acquisitions from different types of clinical acquisitions.
* A region/folder is the highest level that can be exported.  In every .exar1 file seen so far, the path encoded in the file has `Root/Export` above the region.  So in `Root/Export/Investigators/Baker/PCM`, "Investigators" is the region/folder, "Baker" is the exam/investigator, and "PCM" is the program/protocol.  A PDF printout prints the same path with a different root, e.g. `\\Research\Investigators\Baker\PCM\<scan>`.
* Many sequence parameters are not independent.  When adjusting values from the console, often there is a range of new values that you can use that will not change any other parameters, but if you go outside that range, the change will either be forbidden, or will cause other values to change.  Additionally, some values can only take discrete values - values close to an allowed value will snap to the closest allowed value. Each sequence is responsible for checking to make sure that any current set of values is consistent and will allow a scan to start.  This check runs every time a parameter is updated, but this check can only run on the scanner when the sequence is loaded.  If the scanner reads in a protocol from an .exar file that has an inconsistent set of parameters, that scan is labelled as inconsistent, and is grayed out.  You cannot open or modify the scan parameters in that case, only rebuild that scan from a consistent starting parameter set.
* Protocol PDF files can only be generated from consistent protocols, so the parameters in a PDF protocol are by definition consistent.
* A scan whose protocol needs conversion (saved under an older software baseline) is only included in an .exar1 export if "Show inconsistent" is checked in the export options.  If it is unchecked, those scans are silently left out of the archive.
