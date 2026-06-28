# Data Encoding & Schema Evolution

> Staff/Principal deep-dive. Companion to *Designing Data-Intensive Applications* (Kleppmann), Chapter 4: "Encoding and Evolution."

---

## 1. Intro & Why It Matters

Every non-trivial system moves data across a boundary: a function returns an object, a service writes a row, a producer publishes an event, an RPC crosses a network. At each boundary the data lives in **two fundamentally different representations**:

- **In-memory** — objects, structs, lists, hash maps. Optimized for *access* by the CPU: pointers, alignment, cache locality. Meaningless outside the process that owns the address space.
- **Wire / disk (a byte sequence)** — a self-contained, position-independent stream of bytes. Optimized for *transmission and storage*.

The translation in-memory → bytes is **encoding** (a.k.a. serialization, marshalling). The reverse is **decoding** (deserialization, parsing, unmarshalling).

For a staff engineer the interesting part is rarely "how do I turn an object into JSON." It is the **temporal dimension**: in any system that you cannot deploy atomically — which is *every* distributed system and *every* durable datastore — **old code and new code, and old data and new data, coexist**. The encoding format you pick determines whether that coexistence is a non-event or a 3 a.m. incident.

> **The central problem.** Schema changes are inevitable. The format must let you change the schema while keeping:
> - **Backward compatibility** — *new* code can read data written by *old* code.
> - **Forward compatibility** — *old* code can read data written by *new* code.
>
> Backward compatibility is usually easy (you know the old format). Forward compatibility is the hard one: old code must gracefully ignore things it was never told about.

Why both directions matter in practice:

- **Rolling upgrades / canaries.** During a deploy, instance A (new) and instance B (old) talk to each other and to a shared queue. New writes must be readable by old readers (forward) and vice versa (backward).
- **Durable storage.** A row written 5 years ago must be readable by today's code (backward). A row written by today's code is sometimes read by a not-yet-upgraded replica (forward).
- **Event logs.** Kafka topics retain events for days/years. A consumer deployed today reads events produced by every producer version that ever ran.

```
        time ──────────────────────────────────────────────►
producers:   [v1]      [v1,v2 mixed]        [v2]       [v2,v3]
consumers:   [v1]      [v1]                 [v1,v2]    [v3]
                 │                  │                    │
                 ▼                  ▼                    ▼
        old reads old      new writes,            old reads new
        (trivial)          old still reads        (FORWARD compat)
                           (FORWARD compat)
```

---

## 2. The Naïve Trap: Language-Specific Serialization

Most languages ship a built-in way to serialize objects: Java `Serializable`, Python `pickle`, Ruby `Marshal`, .NET `BinaryFormatter`. They are seductive because they need no schema and "just work." **Never use them for anything that crosses a durability or service boundary.** The reasons are foundational, not stylistic:

1. **Tied to one language.** Encoding with `pickle` means every reader must be Python. You have welded the data format to a runtime choice you will regret.
2. **Arbitrary code execution.** Decoding instantiates arbitrary classes. `pickle.loads` / Java `readObject` / `BinaryFormatter` are *remote code execution primitives*. The .NET `BinaryFormatter` was deprecated by Microsoft specifically for this; the Java deserialization gadget-chain CVEs (e.g., Apache Commons Collections) are a whole genre of exploit.
3. **No versioning story.** Compatibility is an afterthought; field renames or class moves break silently.
4. **Bad performance & bloat.** Java serialization is notoriously slow and large.

```python
import pickle

# DO NOT do this with untrusted input — pickle.loads runs __reduce__ payloads.
class Exploit:
    def __reduce__(self):
        import os
        return (os.system, ("echo pwned",))

payload = pickle.dumps(Exploit())   # looks like innocent bytes
pickle.loads(payload)               # -> executes `echo pwned`
```

**Rule:** language-native serialization is fine for an ephemeral, same-process, same-version cache. For storage, IPC, or messaging, use a standardized format.

---

## 3. Textual Formats: JSON, XML, CSV

These dominate because they are human-readable and have a parser in every language. They are *fine defaults* for public-facing APIs where ubiquity beats efficiency. But know the sharp edges:

| Issue | JSON | XML | CSV |
|---|---|---|---|
| Numbers | No int/float distinction; integers > 2^53 lose precision (IEEE-754 double). Twitter's tweet IDs famously had to be sent as strings. | No native number type; everything is text + schema. | Everything is text. |
| Binary data | None — must Base64 (≈ +33% size). | Same. | Same. |
| Schema | Optional (JSON Schema), rarely enforced. | XSD/DTD, verbose. | None; column meaning is positional and fragile. |
| Ambiguity | `,` / encoding edge cases. | Attributes vs elements, namespaces. | Quoting, embedded commas/newlines, no standard dialect (RFC 4180 is loose). |
| Self-describing | Yes (keys repeated in every record). | Yes (very verbose). | Header row only, if present. |

The killer cost at scale: **textual formats are self-describing — every record repeats every field name.** In a Kafka topic with billions of `{"user_id": ..., "event_type": ...}` records you pay for the strings `user_id` and `event_type` a billion times. This is exactly what binary schema-driven formats eliminate.

---

## 4. Binary, Schema-Driven Formats

The three serious contenders for internal dataflow:

- **Apache Thrift** (Facebook, 2007) — RPC + serialization framework.
- **Protocol Buffers / protobuf** (Google, open-sourced 2008) — serialization (+ gRPC for RPC).
- **Apache Avro** (Hadoop ecosystem, Doug Cutting, 2009) — serialization designed for big-data files and Kafka.

All three require you to declare a **schema** (an IDL) up front. The schema is the contract; the on-the-wire bytes are tiny because field names are *not* transmitted. Where they differ profoundly is **how a reader knows which bytes are which field** — and that mechanism is exactly what determines the schema-evolution rules.

### 4.1 Protocol Buffers — field tags

You declare types in a `.proto` file. Each field has a **tag number** and a **wire type**.

```protobuf
// person.proto
syntax = "proto3";
package people;

message Person {
  string user_name   = 1;            // tag 1
  int64  favorite_id = 2;            // tag 2
  repeated string interests = 3;     // tag 3
}
```

On the wire, every field is a key-value pair where the key packs `(tag_number << 3) | wire_type` into a varint, followed by the value. **Field names never appear on the wire — only tag numbers.** A reader uses the tag to look up the field in *its own* schema. The wire type (0 = varint, 1 = 64-bit, 2 = length-delimited, 5 = 32-bit) tells a reader how to skip a field it doesn't recognize, *even without the schema for it* — this is what enables forward compatibility.

```
message Person { user_name="Al", favorite_id=1337 }

bytes:  0A 02 41 6C   10 B9 0A
        │  │  └ "Al"   │  └─ 1337 as varint
        │  └ len=2     └ key: tag=2, wiretype=0 (varint)
        └ key: (1<<3)|2 = 0x0A  tag=1, wiretype=2 (length-delimited)
```

**Integers use varint encoding** (7 data bits per byte, MSB = continuation). Signed ints that can be negative should use `sint32/sint64` (ZigZag) so that small-magnitude negatives don't waste 10 bytes.

Schema-evolution rules (protobuf):
- **Tag numbers are the identity.** Never reuse or change a tag. Renaming a field is free (names aren't on the wire).
- **Adding a field:** give it a new tag. Old readers skip the unknown tag (forward compat); new readers see a default/absent value for old data (backward compat). Therefore **new fields must be optional or have a default** — you cannot add a `required` field. (proto3 has no `required`; in proto2 adding `required` breaks compatibility.)
- **Removing a field:** only remove *optional* fields, and **reserve the tag** so it is never reused: `reserved 2; reserved "favorite_id";`.
- **Changing types:** only between wire-compatible types (e.g., `int32`↔`int64`↔`bool` are varint-compatible with truncation caveats).
- `repeated` ↔ `optional` (singular) is compatible in protobuf: a reader expecting `repeated` of an `optional` value sees 0-or-1 elements.

### 4.2 Thrift — field tags (BinaryProtocol / CompactProtocol)

Thrift is conceptually very close to protobuf: an IDL with numbered fields.

```thrift
struct Person {
  1: required string userName,
  2: optional i64    favoriteId,
  3: optional list<string> interests
}
```

Thrift offers multiple **protocols** (encodings):
- **BinaryProtocol** — straightforward; field type byte + 2-byte field id + value. Larger.
- **CompactProtocol** — like protobuf: varints + field-id deltas + packed type/id, much smaller.

Evolution rules mirror protobuf: tags are identity, add fields with new ids (and a default), remove only `optional` fields. Thrift's `required` is a trap — it is *not* forward/backward-compatible and provides weak runtime guarantees; treat all fields as optional in evolving systems.

### 4.3 Avro — writer's schema + reader's schema, NO tags

Avro is the odd one out and the most elegant for data pipelines. Schema is JSON:

```json
{
  "type": "record",
  "name": "Person",
  "fields": [
    {"name": "userName",   "type": "string"},
    {"name": "favoriteId", "type": ["null", "long"], "default": null},
    {"name": "interests",  "type": {"type": "array", "items": "string"}}
  ]
}
```

The Avro wire format contains **no field tags and no field names — only concatenated values, in schema-declared order.** To decode a single byte you *must* know the schema. So how does it evolve?

Avro distinguishes:
- **Writer's schema** — the schema the data was written with.
- **Reader's schema** — the schema the consuming code expects.

They need not be identical; they need only be **compatible**. At decode time, Avro performs **schema resolution**: it matches fields **by name** (not position, not tag) and reconciles the two.

- A field in the **writer** but not the **reader** → reader ignores it (forward).
- A field in the **reader** but not the **writer** → reader fills in the **default** declared in the reader's schema (backward). *This is why defaults are mandatory for new fields.*
- Field order may differ; matching is by name.
- Reordering, renaming via `aliases`.

The genius: **there is no tag number to manage.** You add/remove fields freely as long as you supply defaults, and you can generate a schema dynamically (e.g., one per Hadoop file, per database table) — which is awkward in protobuf/Thrift because you'd hand-assign tags. This makes Avro ideal for systems with **dynamically generated schemas** (e.g., a tool that dumps any DB table to Avro).

The remaining question: **how does the reader obtain the writer's schema?** Three classic patterns (from DDIA):
1. **Large file with many records** (Hadoop/object store): write the writer's schema once at the head of the file (Avro "object container file").
2. **Database with individually written records**: store a *version number* in each record; keep a table of schema-version → schema.
3. **Sending records over a network**: negotiate the schema at connection setup, or carry a schema ID — which leads us to the **schema registry**.

---

## 5. Schema Registry (Confluent)

In a Kafka deployment you can't prepend a full Avro schema to every 50-byte message — that defeats the purpose. The **Confluent Schema Registry** solves this:

```
Producer                    Schema Registry              Consumer
  │ register schema ───────────►│ (stores schema,         │
  │ ◄─────── schema_id ─────────│  returns int id;        │
  │                             │  enforces compat rules) │
  │                                                       │
  │ publish to Kafka:                                     │
  │   [magic byte][4-byte schema_id][Avro payload] ──────►│ read id
  │                                                       │ fetch schema by id
  │                             │◄───── GET /schemas/id ──│ (cached)
  │                             │────── schema ──────────►│ decode
```

- The wire message is `0x00` + 4-byte **schema ID** + the raw Avro bytes. No field names, no inline schema — just a 5-byte header.
- The registry **enforces a compatibility policy** *at registration time* (`BACKWARD` default, `FORWARD`, `FULL`, `BACKWARD_TRANSITIVE`, etc.). A producer trying to register an incompatible schema is rejected before it can poison the topic — compatibility becomes a CI/CD gate, not a hope.
- Subjects are usually `<topic>-value` / `<topic>-key`.

This is the production-grade answer to "how does the reader get the writer's schema" for streaming. It works with Protobuf and JSON Schema too, not just Avro.

| Confluent compat mode | Allowed changes | Upgrade first |
|---|---|---|
| `BACKWARD` (default) | delete fields, add **optional** fields | **consumers** |
| `FORWARD` | add fields, delete **optional** fields | **producers** |
| `FULL` | add/delete **optional** fields only | either |
| `*_TRANSITIVE` | same, checked against **all** prior versions, not just the latest | — |

---

## 6. Modes of Dataflow

How encoded data actually travels between processes. Each mode has a different compatibility surface.

### 6.1 Via a database
Writer encodes → DB stores bytes → reader decodes, possibly *years* later and possibly from *older* code (a not-yet-upgraded service, or a read replica). Requires **both** backward and forward compatibility.

**The data-outlives-code pitfall** ("data outlives code"): an old service reads a row, deserializes to its struct (dropping fields it doesn't know), then *writes the row back* — silently destroying the new fields. Mitigation: only update touched columns, or carry through unknown fields.

### 6.2 Via services (REST / RPC)
- **REST** — a design philosophy over HTTP: resources, verbs, status codes, content negotiation. Human-debuggable, cache-friendly, ubiquitous; great for public APIs.
- **RPC** — model a remote call as if it were local (Thrift, gRPC, the old CORBA/DCOM). The **fallacy of "location transparency"**: a network call can be slow, can time out, can partially succeed, and the network is unreliable — a local call cannot. Good RPC frameworks (gRPC) embrace this: explicit deadlines, retries with idempotency, streaming, backpressure.

Compatibility: servers are usually updated before clients (or vice versa), so the request encoding needs forward compat and the response backward compat — but both directions matter over long-lived API versions. Public APIs version explicitly (`/v2/`, headers).

### 6.3 Via asynchronous message passing (message brokers)
Producer → broker (Kafka, RabbitMQ, SQS, Pulsar) → one or more consumers. The broker decouples sender and receiver in time and identity. Messages are just encoded blobs; the broker doesn't care. Because retention can be long and consumer fleets are heterogeneous, this is the **strongest case for a schema registry + a strict compatibility policy**.

---

## 7. Worked Example: gRPC / Protobuf with an Evolution Scenario

We'll define a service, then evolve it and reason about compatibility.

### v1 schema

```protobuf
// user_service.proto  (v1)
syntax = "proto3";
package usersvc;

message GetUserRequest { int64 user_id = 1; }

message User {
  int64  user_id   = 1;
  string user_name = 2;
}

service UserService {
  rpc GetUser(GetUserRequest) returns (User);
}
```

### v2 — add a field and a streaming RPC (compatible evolution)

```protobuf
// user_service.proto  (v2)
syntax = "proto3";
package usersvc;

message GetUserRequest { int64 user_id = 1; }

message User {
  int64  user_id   = 1;
  string user_name = 2;
  string email     = 3;            // ADDED, new tag — old clients skip it (forward compat)
  reserved 4;                      // tag 4 was a deleted experimental field; never reuse
}

message ListUsersRequest { int32 page_size = 1; }

service UserService {
  rpc GetUser(GetUserRequest) returns (User);
  rpc ListUsers(ListUsersRequest) returns (stream User);   // ADDED server-streaming RPC
}
```

Why this is safe:
- **Adding `email = 3`:** a v1 client decoding a v2 `User` encounters tag 3, doesn't know it, and *skips it* using the wire-type length prefix → **forward compatible**. A v2 client decoding a v1 `User` (no tag 3) gets `email = ""` (proto3 default) → **backward compatible**.
- **`reserved 4`:** guarantees a removed field's tag is never silently reused for an incompatible meaning.
- **Adding `ListUsers`:** new RPC methods are additive; old clients simply never call them. gRPC dispatches by method name (`/usersvc.UserService/ListUsers`), so unknown methods on an old *server* return `UNIMPLEMENTED` — which a careful client handles.

What would be **unsafe**:
- Reusing tag `2` for a different type, or moving `user_name` to tag `5`.
- Changing `email` from `string` to `repeated string` *of a non-compatible wire type*? (Actually `string`→`repeated string` is wire-incompatible; `int32`→`int64` is fine.)
- Removing `user_name` without `reserved`-ing tag 2.

### Runnable Python: encode v2, decode with v1 reader (forward compatibility, no codegen)

The point below is to *demonstrate the wire mechanics* with pure-Python varint encoding — exactly what protobuf does — so you can see that an unknown tag is skippable.

```python
"""Minimal protobuf-wire demo: prove that an old reader skips a new field.
No protoc required; we hand-roll the varint + length-delimited wire format
for the `User` message and show v1-reader / v2-writer forward compatibility.
"""
from io import BytesIO

# ---- wire-format primitives (the actual protobuf encoding) ----
def encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)

def read_varint(buf: BytesIO) -> int:
    result = shift = 0
    while True:
        b = buf.read(1)[0]
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result
        shift += 7

def tag(field_no: int, wire_type: int) -> bytes:
    return encode_varint((field_no << 3) | wire_type)

WT_VARINT, WT_LEN = 0, 2  # the two wire types we need

def encode_string(field_no: int, s: str) -> bytes:
    data = s.encode("utf-8")
    return tag(field_no, WT_LEN) + encode_varint(len(data)) + data

def encode_int(field_no: int, v: int) -> bytes:
    return tag(field_no, WT_VARINT) + encode_varint(v)

# ---- v2 WRITER: User{user_id=1, user_name="al", email="al@x.io"} ----
def encode_user_v2(user_id, user_name, email) -> bytes:
    return (encode_int(1, user_id)
            + encode_string(2, user_name)
            + encode_string(3, email))   # tag 3 is unknown to v1

# ---- v1 READER: only knows tags 1 (int) and 2 (string); skips the rest ----
V1_FIELDS = {1: ("user_id", "int"), 2: ("user_name", "str")}

def decode_user_v1(blob: bytes) -> dict:
    buf, out = BytesIO(blob), {}
    while buf.tell() < len(blob):
        key = read_varint(buf)
        field_no, wire_type = key >> 3, key & 0x07
        if field_no in V1_FIELDS:
            name, kind = V1_FIELDS[field_no]
            if kind == "int":
                out[name] = read_varint(buf)
            else:
                length = read_varint(buf)
                out[name] = buf.read(length).decode("utf-8")
        else:
            # UNKNOWN FIELD (e.g. email/tag 3) — skip using wire type.
            # This single branch is the whole forward-compatibility mechanism.
            if wire_type == WT_VARINT:
                read_varint(buf)
            elif wire_type == WT_LEN:
                buf.read(read_varint(buf))
            else:
                raise ValueError(f"cannot skip wire_type {wire_type}")
    return out

if __name__ == "__main__":
    wire = encode_user_v2(42, "al", "al@x.io")
    print("v2 wire bytes:", wire.hex())
    print("v1 reader sees:", decode_user_v1(wire))
    # -> {'user_id': 42, 'user_name': 'al'}   email silently skipped, no crash
    assert decode_user_v1(wire) == {"user_id": 42, "user_name": "al"}
    print("OK: old reader skipped the new field (forward compatible)")
```

In a real project you would instead run `protoc`/`grpcio-tools` to generate classes and a gRPC stub; the *wire behavior above is identical* to what the generated code does. A realistic codegen-based flow:

```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. user_service.proto
```

```python
# Using generated classes (illustrative)
import user_service_pb2 as pb
u = pb.User(user_id=42, user_name="al", email="al@x.io")
raw = u.SerializeToString()                  # v2 encode
# A process built from v1 .proto would do User.ParseFromString(raw) and
# simply not have an `email` attribute — the unknown field is preserved
# in the message's unknown-field set and re-emitted on re-serialization.
```

> Note: protobuf retains **unknown fields** on parse (proto3 since 3.5) and re-serializes them, which protects the "read-modify-write" path in §6.1 — old code won't destroy new fields it round-trips. Avro does *not* do this automatically; design accordingly.

---

## 8. Comparison Table

| Dimension | JSON / XML | Thrift | Protocol Buffers | Avro |
|---|---|---|---|---|
| Encoding | Text, self-describing | Binary, tagged | Binary, tagged (varint) | Binary, **no tags/names** |
| Schema required to decode? | No | Yes (IDL → codegen) | Yes (IDL → codegen) | **Yes — writer's schema mandatory** |
| Field identity | Name (in payload) | **Tag number** | **Tag number** | **Field name** (schema resolution) |
| Add field | trivial | new tag + default | new tag + default | new field + **default** |
| Remove field | trivial | remove `optional`; keep tag retired | remove + `reserved` tag | remove (reader uses no default) |
| Rename field | breaks readers | free (name not on wire) | free (name not on wire) | needs `aliases` |
| Forward compat mechanism | parser ignores keys | wire-type skip of unknown tag | wire-type skip of unknown tag | schema resolution drops unknown |
| Backward compat mechanism | absent key → null | default for missing tag | default for missing tag | reader's-schema **default** |
| Dynamically generated schemas | n/a | painful (manual tags) | painful (manual tags) | **excellent** |
| Code generation | none | yes | yes | optional (also has generic API) |
| Sweet spot | public APIs, configs, debugging | RPC + storage (legacy/FB) | gRPC microservices, mobile | Kafka, Hadoop, data lakes |
| RPC story | REST/none | built-in | gRPC | none (data only) |

---

## 9. Trade-offs & Guidance (Staff Lens)

- **Default for service-to-service RPC:** Protobuf + gRPC. Mature tooling, tag-based evolution, streaming, deadlines. Cost: codegen in the build, binary opacity (need tooling to read on the wire).
- **Default for event streaming / data lake:** Avro + Schema Registry. Field-name resolution and registry-enforced compatibility scale better when schemas are many and machine-generated. Cost: you *always* need the writer's schema available.
- **Default for public/partner APIs:** JSON over REST (optionally JSON Schema / OpenAPI). Ubiquity and debuggability win; pay the size/precision tax. Watch the 2^53 integer trap.
- **Never:** language-native serialization across boundaries (security + lock-in).
- **Always:** make compatibility a CI gate. The registry/`buf breaking`/`protolock` should fail the build on an incompatible schema change — don't rely on review.
- **Treat tags/field-names as a permanent allocation.** Reserve retired tags. Treat the schema as an append-mostly ledger.
- **Decide who upgrades first** (consumers vs producers) and pick the registry compatibility mode to match (`BACKWARD` if consumers go first — the common default).

---

## 10. Key Takeaways

1. Data crosses a boundary as **bytes**; encoding/decoding is the bridge, and the format choice is really a **schema-evolution policy** choice.
2. The hard property is **forward compatibility** — old code must skip what it doesn't understand. Tag-based formats (protobuf/Thrift) skip via wire type; Avro drops via schema resolution.
3. **Protobuf/Thrift use field tag numbers as identity** (rename free, reuse forbidden, reserve removed tags). **Avro uses field names** with a writer-schema/reader-schema resolution, and **mandatory defaults** for added fields.
4. **Avro shines for dynamically generated schemas** (data lakes); protobuf shines for hand-curated RPC contracts.
5. The **Confluent Schema Registry** turns compatibility into an enforced, centrally-checked policy and shrinks the wire payload to a 5-byte schema-id header.
6. Dataflow modes — DB, RPC/REST, async messaging — each have a distinct compatibility surface; **data outlives code**, so storage demands both compat directions, and beware read-modify-write dropping unknown fields.
7. Never use `pickle` / Java `Serializable` / `BinaryFormatter` across trust boundaries — they are RCE primitives with no evolution story.

---

## References

- Martin Kleppmann, *Designing Data-Intensive Applications* (O'Reilly, 2017), **Chapter 4: Encoding and Evolution**.
- Google, *Protocol Buffers Language Guide & Encoding* — https://protobuf.dev/programming-guides/encoding/
- Apache Avro Specification — https://avro.apache.org/docs/current/specification/
- Apache Thrift: Slee, Agarwal, Kwiatkowski, *Thrift: Scalable Cross-Language Services Implementation* (Facebook white paper, 2007).
- Confluent, *Schema Registry & Schema Evolution and Compatibility* documentation.
- A. Birrell & B. Nelson, *Implementing Remote Procedure Calls* (ACM TOCS, 1984) — the original RPC paper and the source of the location-transparency caution.
- Deutsch & Gosling, *The Eight Fallacies of Distributed Computing*.
