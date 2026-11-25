# DNS Resolution

The Domain Name System turns a name such as `www.example.com` into the data a
client needs to connect, most often an IP address. It is a distributed,
hierarchical, heavily cached database. Almost every network operation starts
with a DNS lookup, so a slow or broken resolver makes everything feel broken.

## The hierarchy

Names are read right to left, each label delegating to the next:

    .                      root (13 named server clusters, a–m)
    └── com.               top-level domain (TLD)
        └── example.com.   the zone, run by its authoritative servers
            └── www        a record inside that zone

No single server knows everything. Each level knows only who is responsible for
the level below it.

## Who talks to whom

### Stub resolver

The library inside your OS or application (`getaddrinfo`). It does almost
nothing: it sends the question to a configured recursive resolver and waits.

### Recursive resolver

Run by your ISP, your company, or a public service (1.1.1.1, 8.8.8.8). It does
the real work on the client's behalf and caches the results.

### Authoritative server

Holds the actual records for a zone and answers only for names it is
responsible for. It never chases referrals.

## Walking through one lookup

Resolving `www.example.com` with a cold cache:

1. The stub asks the recursive resolver for `www.example.com A`.
2. The resolver asks a **root** server. The root does not know the answer but
   replies with a *referral*: the name servers for `com.`.
3. The resolver asks a **`.com` TLD** server, which refers it to the name
   servers for `example.com.`.
4. The resolver asks an **authoritative** server for `example.com.`, which
   returns the A record.
5. The resolver caches every step and returns the answer to the stub.

The stub's request is *recursive* ("give me the final answer"). The resolver's
own queries are *iterative* ("tell me what you know, I will follow up"). In
practice steps 2 and 3 are nearly always served from cache: the TLD delegations
have TTLs of one or two days.

```bash
# show every referral from the root down
dig +trace www.example.com

# ask one specific resolver, print only the answer
dig @1.1.1.1 www.example.com A +short

# remaining TTL is the second column of the answer section
dig www.example.com A +noall +answer
```

## Record types

| type  | meaning                                             | example value                    |
|-------|-----------------------------------------------------|----------------------------------|
| A     | name → IPv4 address                                 | `93.184.216.34`                  |
| AAAA  | name → IPv6 address                                 | `2606:2800:220:1::1`             |
| CNAME | alias: this name is another name                    | `www` → `web.cdn.example.net.`   |
| NS    | which servers are authoritative for this zone       | `ns1.example.com.`               |
| MX    | mail servers for the domain, with a preference      | `10 mail.example.com.`           |
| TXT   | free text (SPF, domain verification, DKIM)          | `"v=spf1 -all"`                  |
| SOA   | zone metadata: primary server, serial, timers       | one per zone                     |
| PTR   | reverse lookup: address → name                      | `34.216.184.93.in-addr.arpa.`    |

A CNAME must be the only record at its name, which is why a zone apex
(`example.com` itself) cannot be a CNAME; providers offer ALIAS or ANAME
flattening to work around that.

## Caching and TTL

Every record carries a **TTL** (time to live) in seconds, set by the zone
owner. A resolver may serve the record from cache until the TTL expires, and
must then ask again. Caches exist at several layers: the recursive resolver, the
OS, and sometimes the browser or the application runtime.

- Long TTLs (hours to days) mean fewer queries and resilience when the
  authoritative servers are unreachable, but slow propagation of changes.
- Short TTLs (30–300 s) allow fast failover and traffic steering, at the price
  of more queries and more dependence on the authoritative servers.
- Before a planned migration, lower the TTL *at least one old-TTL in advance*.
  Lowering it at the moment of the change does nothing for clients that already
  cached the old value.

"DNS propagation" is not a push. It is old cache entries expiring at different
times in different resolvers.

### Negative caching

A "this name does not exist" answer (NXDOMAIN) is cached too, for the duration
given in the zone's SOA record (RFC 2308). This is why a record created seconds
after someone queried for it can appear to be missing for several minutes.

## Transport and privacy

Classic DNS is a single UDP datagram to port 53, unencrypted. Answers that do
not fit are truncated and retried over TCP. Anyone on the path can read and,
without DNSSEC, forge them.

- **DNSSEC** adds signatures so a resolver can verify that an answer really
  comes from the zone owner. It gives authenticity, not confidentiality.
- **DNS over TLS (DoT)** and **DNS over HTTPS (DoH)** encrypt the hop between
  the stub and the recursive resolver, hiding queries from the local network.

## Failure modes

| what you see | usual meaning                                                    |
|--------------|------------------------------------------------------------------|
| NXDOMAIN     | the name does not exist (or you have a typo)                     |
| SERVFAIL     | the resolver could not get a valid answer: broken delegation, DNSSEC validation failure, authoritative servers down |
| timeout      | resolver unreachable, or UDP 53 blocked                          |
| NOERROR, no answer | the name exists but has no record of the requested type    |

When "the site is down" but `curl` by IP address works, look at DNS first:
compare `dig` against two different resolvers, then against the authoritative
server directly. Different answers mean a caching or propagation issue, not an
outage of the service itself.
