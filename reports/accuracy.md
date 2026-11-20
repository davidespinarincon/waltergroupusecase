# Accuracy report (claude / claude-haiku-5-5, n=200)

| Metric | Value |
|---|---|
| Decision accuracy | **99.5%** |
| Intent accuracy | 100.0% |
| Request count correct | 100.0% |
| Field `origin` | 98.4% |
| Field `destination` | 98.4% |
| Field `pickup_date` | 97.8% |
| Field `weight_kg` | 98.4% |

## Confusion matrix (rows = expected, columns = decided)

| expected \ got | SEND_PRICE | ASK_CUSTOMER | HANDOVER_HUMAN | RETRY_LATER | IGNORE |
|---|---|---|---|---|---|
| SEND_PRICE | 73 | 1 | 0 | 0 | 0 |
| ASK_CUSTOMER | 0 | 46 | 0 | 0 | 0 |
| HANDOVER_HUMAN | 0 | 0 | 80 | 0 | 0 |

## By scenario

| scenario | n | accuracy |
|---|---|---|
| complete | 63 | 98.4% |
| expensive | 11 | 100.0% |
| missing_route | 3 | 100.0% |
| missing_weight | 26 | 100.0% |
| multi_complete | 11 | 100.0% |
| other | 33 | 100.0% |
| overweight | 16 | 100.0% |
| past_date | 7 | 100.0% |
| reference | 13 | 100.0% |
| vague_date | 17 | 100.0% |

## By language

| language | accuracy |
|---|---|
| de | 98.4% |
| en | 100.0% |
| es | 100.0% |
| fr | 100.0% |
| it | 100.0% |

## Errors (1; 0 would have auto-sent a price wrongly)

- **S104** complete/de: expected SEND_PRICE, got ASK_CUSTOMER - missing pickup_date
