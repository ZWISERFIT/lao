# LAO — Your Agent's Reliability Layer

**3 things your Agent keeps doing wrong. LAO fixes all three.**

> **AI agents fail silently. LAO makes failures visible and fixable.**

| Your Agent's Problem | How LAO Solves It |
|:--|:--|
| 🧠 **Forgets what you told it** | Behavioral Memory Chain — remembers your agent's identity, constraints, and brand promises across sessions |
| 🤥 **Makes things up (hallucinates)** | Intent Validation + Output Compliance — detects when your agent is about to do something wrong and intercepts before execution |
| 💸 **Burns your tokens** | Key Anchor Engine — intelligently prunes context noise while preserving critical anchors (saves up to 99% tokens, benchmarked) |

---

## 3 lines. 3 minutes. See the difference.

```bash
pip install lao-human-calibration
```

```python
from lao import LAOAgent
from lao import wrap

# Your existing agent
my_agent = SomeAgent(...)

# Wrap it with LAO — same interface, now trustworthy
trusted_agent = LAOAgent(wrap=my_agent).with_memory().with_verification()
```

---

## What makes LAO different?

> Most tools solve **one** problem: memory OR compression OR safety.  
> LAO solves **all three** — because forgetting, hallucinating, and burning tokens are the same problem expressed three ways.

| | Memory (Mem0/Letta) | Compression (Headroom/Caveman) | Safety (NeMo/Prismor) | **LAO** |
|:--|:--:|:--:|:--:|:--:|
| Remembers what matters | ✅ | ❌ | ❌ | ✅ |
| Verifies before executing | ❌ | ❌ | ⚠️ (security only) | ✅ |
| Saves tokens intelligently | ❌ | ✅ | ❌ | ✅ |
| Personal BMC model | ❌ | ❌ | ❌ | ✅ |
| Open source | ✅ | ✅ | ✅ | ✅ |

---

## We eat our own dog food

Our 9-Agent cluster runs on LAO 24/7. In the last 24 hours:

- **99.0%** token compression across 3 production scenarios (147K → 1.5K tokens)
- **62.2%** memory density improvement (37 files, 66K → 25K tokens with zero critical loss)
- **114** automatically hardened anchors from real failures — once an agent makes a mistake, it never repeats

We're honest: **0 external Trust Events yet.** Our agents are producing them internally, but we're waiting for the first developer to wrap their own agent with LAO and share their Trust Event.

---

## Runtime Protection in Action

> Every failure is a R-Law: an immutable, versioned anchor that makes the same class of error structurally impossible going forward.

On August 8, 2026, our 9-Agent cluster ran a full 24-hour cycle under autonomous governance. **5 distinct failures were detected, repaired, anchored, and permanently prevented. Zero repeats. Zero founder intervention in the repair loop.**

### The chain behind every fix

```
failure → detection → repair → prevention → R-Law anchor
```

### Real failures, permanently prevented

| Failure | Repair | R-Law anchor |
|---------|--------|:--:|
| Agent pushed a platform the founder never requested (hallucination) | Intent Validation gate intercepted before execution | A-OUTPUT-001/002/003 |
| Agent knew the right pattern but executed the wrong port — twice | Structural prevention, not a better prompt | Port-confusion anchor |
| 3 agents hit the same URL error independently | One gate stopped all three | Unified URL gate |
| Silent failure with no error surfaced to the user | Trust ledger logged every event | Event-log anchor |
| Correction didn't persist (skill amnesia) | Correction anchored beyond working memory | Cross-skill retention anchor |

**5 failures, 5 R-Laws, 114 total anchors hardened. 0 repeats.**

Your agent fails silently too. Wrap it with LAO and make that failure visible the moment it happens — and fixable permanently.

---

## Documentation

- [Getting Started](https://github.com/ZWISERFIT/lao/wiki)
- [Trust Event Schema](https://github.com/ZWISERFIT/lao/wiki/Trust-Event-Schema)
- [API Reference](https://github.com/ZWISERFIT/lao/wiki/API)
- [Trust Builders Community](https://github.com/ZWISERFIT/lao/discussions/5)

---

## Contributing

We're building the first open-source Agent Trust Infrastructure. Every contribution counts.

- 🐛 Found a bug? [Open an Issue](https://github.com/ZWISERFIT/lao/issues/new)
- 💡 Have an idea? [Start a Discussion](https://github.com/ZWISERFIT/lao/discussions)
- 🔧 Want to contribute? See [CONTRIBUTING.md](CONTRIBUTING.md)
- 🏗️ Join Trust Builders — [Discussion #5](https://github.com/ZWISERFIT/lao/discussions/5)

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

<p align="center">
  <b>LAO</b> · Agent Trust Infrastructure · Built by <a href="https://github.com/ZWISERFIT">ZWISERFIT</a>
</p>
