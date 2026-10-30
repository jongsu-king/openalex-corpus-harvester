# Running GROBID locally

GROBID **0.9.1** was built from source and tested on an open-access paper on macOS ARM64 on 2026-10-29. This installation uses OpenJDK 21 and the release's ARM64 Wapiti and pdfalto binaries, without Docker. It includes models for headers, full text, citations, reference segmentation, authors and affiliations, figures, tables, and funding. DeLFT neural models were not installed.

Upstream documentation: [installation](https://grobid.readthedocs.io/en/latest/Install-Grobid/), [the 0.9.1 release](https://github.com/grobidOrg/grobid/releases/tag/0.9.1), and [FAQ](https://grobid.readthedocs.io/en/latest/Frequently-asked-questions/).

## Install and control the service

Run the helper from the application directory (`openalex_corpus/`). The tested JDK is at `/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home`; set `GROBID_JAVA_HOME` to use another installation. No global Java symlink or shell-profile change is required.

```bash
# Install for the first time, or reproduce the build.
.venv/bin/python scripts/grobid_service.py install

# Start the background service, inspect it, and stop it.
.venv/bin/python scripts/grobid_service.py start
.venv/bin/python scripts/grobid_service.py status
.venv/bin/python scripts/grobid_service.py stop
```

The API listens on `http://127.0.0.1:8070`; the administration port is `127.0.0.1:8071`. Both bind only to loopback. The background service survives the shell command that starts it, but it is not registered to start automatically after login or reboot. Before stopping a process, the helper checks its PID and creation time to verify that it belongs to this service.

The project's request adapter sets header, citation, and funder consolidation to `0`. This disables external metadata enrichment for those requests. It preserves extracted values, raw citations, coordinates, and the original TEI, and labels the extraction `unverified`. It also records the software identifier, version, and extraction time from the TEI `application` element. Clients that call GROBID directly must set their own consolidation options.

## Installation files and resource limits

| Path | Contents |
|---|---|
| `data/services/grobid/grobid-0.9.1/` | Release source, models, and build output |
| `data/services/grobid/installation.json` | Version, JDK, and source checksum |
| `data/services/grobid/runtime/grobid.local.yaml` | Generated local service configuration |
| `data/services/grobid/runtime/service.log` | Service log |
| `data/services/grobid/runtime/build.log` | Installation/build log |

The Java heap is capped at 1,536 MiB and direct memory at 256 MiB. Wapiti also allocates native memory outside the Java heap: measured RSS after the first full-text request was about 1.62 GiB (1,659 MiB). The service has one processing engine, and pdfalto is limited to 1,024 MiB and 120 seconds. These are component limits, not a combined memory ceiling for GROBID, Docling, and OCR; account for all running processes when sizing a machine.

The JVM option `-Dorg.grobid.config=...` points GROBID core to the same configuration file that Dropwizard reads. In the tested service, the log entry `Number of Engines in pool active/max: 1/1` confirmed that the single-engine setting took effect.

The installer verifies the source archive against this SHA-256:

```text
e5fc91e2231d2ef0ae4e7f9d098cd84a702fb14fc823d2f70ae048481f15c374
```

GROBID 0.9.1's `installDist` build encountered duplicate langdetect JARs. The helper handles this through a local Gradle init script that sets `DuplicatesStrategy.EXCLUDE`, matching the release's `distZip` behavior without modifying upstream source. The build uses at most two workers and a 1 GiB Gradle heap.

## Exercise the real integration

Start the service, then run:

```bash
OPENALEX_RUN_GROBID=1 .venv/bin/pytest -q tests/test_grobid_integration.py
```

Set `GROBID_TEST_PDF` to test another archived PDF. Use a paper with references: the test checks service health, a real full-text request, the TEI file, structured references, authors, raw citations, coordinates, software version, and preservation of the original PDF checksum.

The recorded run used archived Work `W4402950921`, *Docling Technical Report*. It produced approximately 103 KB of TEI, 108 body/structure items, and 16 structured references. All 16 references had raw citation text and coordinates. The first request took about 14 seconds; after a service restart, the integration test reported **1 passed**. Evidence is retained under `data/validation/grobid/` in:

- `integration_evidence.json`
- `W4402950921.tei.xml`
- `W4402950921.tei.parsed.json`

The run also exposed an extraction error: GROBID selected the title of a DocLayNet example embedded in the PDF as the document's header title. That value must not overwrite OpenAlex's *Docling Technical Report* title. Keep the mismatch available for review. GROBID also reported incomplete figure/table structures. A successful request establishes that the integration works; the TEI still needs scrutiny for metadata, table cells, and equation superscripts or subscripts.
