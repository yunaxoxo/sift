# Gradient Descent and Optimizers

Training a neural network means finding parameters θ that minimise a loss L(θ).
Every optimizer in common use is a variation on one idea: compute the gradient,
then take a step downhill.

## Plain gradient descent

    θ ← θ − η · ∇L(θ)

η is the **learning rate**. The gradient points in the direction of steepest
increase, so we move against it.

### Batch, stochastic, mini-batch

| variant      | gradient computed on        | character                                   |
|--------------|-----------------------------|---------------------------------------------|
| batch        | the whole dataset           | exact but one step per pass; impractical    |
| stochastic   | a single example            | very noisy, cheap, cannot use vectorisation |
| mini-batch   | 32 to 4096 examples         | the practical default                       |

In everyday usage "SGD" means mini-batch SGD. The noise from sampling is not
only a cost: it helps the optimizer leave sharp minima and saddle points.

## The learning rate matters most

- **Too small**: the loss falls smoothly but very slowly, and training can stall
  on plateaus.
- **Too large**: steps overshoot the minimum. The loss bounces up and down from
  one step to the next, then climbs, and finally becomes `NaN` when the weights
  overflow. A training curve that oscillates wildly or suddenly blows up is the
  signature of a learning rate that is too high.
- **About right**: the loss drops quickly at first, then steadily.

If a run diverges, the first thing to try is dividing the learning rate by 3 to
10. The second is gradient clipping. Only after that is it worth suspecting the
data or the model.

## Momentum

Plain SGD zig-zags across narrow valleys: the gradient is large across the
valley and small along it. Momentum keeps an exponentially decaying average of
past gradients and steps along that instead:

    v ← β · v + ∇L(θ)
    θ ← θ − η · v

With β = 0.9 the components that keep changing sign cancel out, and the
consistent ones accumulate, up to roughly 1 / (1 − β) = 10 times the plain step.
Nesterov momentum evaluates the gradient at the look-ahead point θ − ηβv, which
corrects overshoot slightly earlier.

## Adaptive methods

### RMSProp

Different parameters see gradients of very different scale. RMSProp divides each
parameter's step by a running root-mean-square of its recent gradients, so
parameters with consistently large gradients take smaller steps and rarely
updated parameters take larger ones.

### Adam

Adam combines both ideas. It tracks a running mean of the gradient (the first
moment, as in momentum) and a running mean of the squared gradient (the second
moment, as in RMSProp):

    m ← β₁ · m + (1 − β₁) · g
    v ← β₂ · v + (1 − β₂) · g²

Both averages start at zero, so during the first steps they are biased towards
zero: after one step m equals only (1 − β₁) · g, a tenth of the true gradient
when β₁ = 0.9. With β₂ = 0.999 the second moment needs about a thousand steps to
warm up. Adam therefore applies a **bias correction**, dividing each average by
the total weight it has accumulated so far:

    m̂ = m / (1 − β₁ᵗ)
    v̂ = v / (1 − β₂ᵗ)
    θ ← θ − η · m̂ / (√v̂ + ε)

At t = 1 the correction exactly cancels the initial shrinkage; as t grows, β₁ᵗ
and β₂ᵗ go to zero and the correction fades away. Without it, the earliest
updates would be far too large, because v is underestimated much more strongly
than m and it sits in the denominator.

Defaults that work surprisingly often: β₁ = 0.9, β₂ = 0.999, ε = 1e−8,
η = 1e−3 (or 1e−4 to 5e−5 when fine-tuning a pretrained model).

### AdamW

Adding L2 regularisation to the loss does not behave like weight decay under
Adam, because the penalty's gradient is rescaled by 1 / √v̂ along with
everything else. AdamW **decouples** the decay: it shrinks the weights directly
by η · λ · θ, outside the adaptive update. It is the default for transformers.

```python
import torch

optimizer = torch.optim.AdamW(
    model.parameters(),
    lr=3e-4,              # peak learning rate, reached after warmup
    betas=(0.9, 0.999),
    weight_decay=0.01,    # decoupled: not part of the gradient
)

for batch in loader:
    loss = model(batch).loss
    loss.backward()
    # clip the global gradient norm before stepping
    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
    optimizer.step()
    scheduler.step()
    optimizer.zero_grad()
```

## Learning rate schedules

A single constant rate is rarely best. Early in training large steps are
productive; near the end they prevent settling into a minimum.

### Warmup

Start from a very small learning rate and increase it linearly to the peak over
the first few hundred or few thousand steps. Why this helps:

- At initialisation the gradients are large and point in erratic directions; a
  full-size step can push the network into a region it never recovers from.
- Adam's second-moment estimate is unreliable for the first steps, even with
  bias correction, so its per-parameter step sizes are poorly calibrated.
- Normalisation statistics and residual-stream scales have not settled yet.

Warmup is close to mandatory for transformers and for large batch sizes. A
common choice is 1–5% of the total number of steps.

### Decay

After warmup the rate comes back down:

- **Cosine decay**: a smooth half-cosine from the peak to nearly zero. The usual
  default today.
- **Step decay**: multiply by 0.1 at fixed epochs. The classic ResNet recipe.
- **Linear decay**: a straight line to zero, common for fine-tuning.
- **Reduce on plateau**: lower the rate when the validation loss stops
  improving.

## Gradient clipping

Rescale the gradient when its global norm exceeds a threshold, typically 1.0.
The direction is preserved and only the step length is limited. Clipping stops
one bad batch from destroying a run, and is standard for RNNs and transformers.

## What I actually do

1. Start with AdamW, warmup followed by cosine decay, and gradient clipping at
   1.0.
2. Sweep the peak learning rate on a log scale (1e−5 to 1e−2). It matters more
   than every other hyperparameter combined.
3. Watch the loss and the gradient norm together. A rising gradient norm is
   visible a few hundred steps before the loss diverges.
4. For convolutional vision models, SGD with momentum 0.9 and step or cosine
   decay still tends to generalise slightly better than Adam.
