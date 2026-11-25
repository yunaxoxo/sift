# The TLS Handshake

TLS gives a TCP connection three properties: **confidentiality** (nobody on the
path can read it), **integrity** (nobody can alter it undetected) and
**authentication** (the client knows it is talking to the real server). The
handshake is where the two sides agree on keys and the server proves its
identity. These notes follow TLS 1.3 (RFC 8446) and point out where 1.2 differs.

## TLS 1.3 in one round trip

```text
Client                                           Server

ClientHello
  + supported versions, cipher suites
  + key_share (client's ephemeral public key)
  + server_name (SNI)              -------->
                                                 ServerHello
                                                   + key_share
                                           {EncryptedExtensions}
                                           {Certificate}
                                           {CertificateVerify}
                                   <--------     {Finished}
{Finished}                         -------->
[Application data]                 <------->     [Application data]
```

Braces mean "already encrypted". After a single round trip both sides hold the
same traffic keys and the client can send its request.

### Step by step

1. **ClientHello.** The client guesses which key-exchange group the server will
   accept (usually X25519) and sends a public key for it straight away. That
   guess is what removes a round trip compared with TLS 1.2.
2. **ServerHello.** The server picks the cipher suite and returns its own
   ephemeral public key. Both sides can now compute the shared secret, so
   everything after this message is encrypted.
3. **Certificate.** The server's certificate chain.
4. **CertificateVerify.** A signature, made with the certificate's private key,
   over a hash of the whole handshake so far. This proves possession of the
   private key and binds the certificate to *this* handshake.
5. **Finished.** A MAC over the transcript in each direction. If an attacker
   changed anything, for example by stripping strong cipher suites from the
   ClientHello, the MACs do not match and the connection is torn down.

## Key exchange and forward secrecy

TLS 1.3 only allows **ephemeral Diffie–Hellman** key exchange (ECDHE, or DHE
over finite fields). Each side generates a fresh key pair for this one
connection, exchanges public keys, and derives the same shared secret. The
ephemeral private keys are discarded afterwards.

That gives **forward secrecy**: the server's long-term certificate key is used
only to *sign* the handshake, never to encrypt the session secret. Someone who
records traffic today and steals the certificate's private key next year still
cannot decrypt the recording, because the per-connection keys no longer exist.

TLS 1.2 also allowed *static RSA* key exchange, where the client encrypted the
pre-master secret with the server's public key. One stolen key then decrypted
every past session ever recorded. TLS 1.3 removed it.

## Certificates and authentication

The certificate binds a public key to one or more names, and is signed by a
certificate authority (CA). The client checks that:

- the chain leads to a root CA in its trust store,
- every signature in the chain verifies,
- the certificate is within its validity period,
- the hostname it asked for matches a **Subject Alternative Name** (the Common
  Name field is no longer consulted),
- the certificate has not been revoked (OCSP stapling, CRLs, or browser-shipped
  revocation lists).

The server usually sends its leaf certificate and the intermediates, but not the
root. A missing intermediate is the classic misconfiguration: it works in
browsers that cached the intermediate elsewhere, and fails in `curl`.

~~~bash
# show the chain the server actually sends, and the negotiated parameters
openssl s_client -connect example.com:443 -servername example.com -showcerts

# only the dates and names of the leaf certificate
echo | openssl s_client -connect example.com:443 2>/dev/null \
  | openssl x509 -noout -dates -subject -ext subjectAltName
~~~

Authentication is one-way by default. **Mutual TLS** (mTLS) adds a
CertificateRequest so the client presents a certificate as well; it is common
between services inside a data centre.

## Resumption and 0-RTT

A full handshake costs a round trip plus an asymmetric signature. For repeat
visits, the server sends a **session ticket** after the handshake. The client
later offers it as a pre-shared key (PSK), which skips the certificate and
signature.

### 0-RTT early data

With a PSK the client can go further and send application data in its *first
flight*, encrypted under a key derived from the PSK, before hearing from the
server at all. That saves a full round trip. It has two real costs:

- **No forward secrecy for the early data.** It is protected only by the PSK,
  not by a fresh Diffie–Hellman exchange.
- **Replay.** Nothing in the first flight comes from the server, so an attacker
  who captures the client's first flight can send it again and the server may
  process the request twice.

0-RTT is therefore safe only for idempotent requests. Servers and CDNs normally
restrict it to `GET` without side effects, add single-use tickets or a replay
cache, and reject early data on anything that changes state. `POST /transfer`
must never be accepted as early data.

## What changed from TLS 1.2

| aspect                | TLS 1.2                            | TLS 1.3                         |
|-----------------------|------------------------------------|---------------------------------|
| round trips (full)    | 2                                  | 1                               |
| key exchange          | RSA, DHE or ECDHE                  | (EC)DHE only                    |
| forward secrecy       | optional                           | always                          |
| handshake encryption  | certificate sent in clear          | encrypted after ServerHello     |
| ciphers               | CBC, RC4, AEAD                     | AEAD only (AES-GCM, ChaCha20-Poly1305) |
| resumption            | session IDs, tickets               | PSK, optional 0-RTT             |

## Debugging checklist

- Handshake failure straight after ClientHello: no shared protocol version or
  cipher suite.
- `certificate verify failed`: missing intermediate, expired certificate,
  hostname mismatch, or a client trust store that lacks the root.
- Works in one data centre but not another: SNI missing, so the load balancer
  serves its default certificate.
- Latency: count round trips. TCP (1) + TLS 1.3 (1) before the first byte of
  the request; QUIC folds both into one.
