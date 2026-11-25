# Attention in Transformers

Notes on "Attention Is All You Need" (Vaswani et al., 2017) and what the pieces
are for.

## The problem attention solves

An RNN reads a sequence one token at a time and has to squeeze everything it has
seen into a fixed-size hidden state. Information about token 3 must survive
hundreds of updates to influence token 400, and the gradient has to travel the
same long path back. Attention lets every position look at every other position
**directly**, in a single step, so the path between any two tokens has length
one. It also removes the sequential dependency: all positions are computed in
parallel.

## Scaled dot-product attention

Each token is projected into three vectors:

- a **query** q: what this position is looking for,
- a **key** k: what this position offers to others,
- a **value** v: the content that is passed on if the position is attended to.

Stacking these as matrices Q, K and V for the whole sequence:

    Attention(Q, K, V) = softmax( Q · Kᵀ / √d_k ) · V

Reading the formula from the inside out:

1. `Q · Kᵀ` is an n × n matrix of dot products: the raw compatibility between
   every query and every key.
2. Divide by `√d_k`, where d_k is the dimension of the key vectors.
3. A softmax over each row turns the scores into weights that sum to 1.
4. Multiply by V: each output is a weighted average of the value vectors.

### Why divide by the square root of d_k

The dot product of two d_k-dimensional vectors with independent components of
unit variance has variance d_k, so its typical magnitude grows like √d_k. With
d_k = 512 the raw scores are routinely in the tens. A softmax given inputs that
large **saturates**: almost all of the weight goes to one position, and the
gradient with respect to every other score is close to zero. The model would
start out attending in a hard, nearly one-hot way and would learn very slowly.

Dividing by √d_k rescales the scores back to unit variance whatever the
dimension, which keeps the softmax in the range where its gradients are useful.
It acts as a fixed temperature chosen so that initialisation is well behaved.

## Multi-head attention

A single softmax can only express one weighting per position. Multi-head
attention runs h attention operations in parallel, each with its own learned
projections of dimension d_model / h, and concatenates the results:

    head_i = Attention(Q · W_i^Q, K · W_i^K, V · W_i^V)
    output = Concat(head_1, ..., head_h) · W^O

Different heads learn different relations: one may follow syntactic dependencies,
another coreference, another simply the previous token. Because each head works
in a smaller subspace, the total cost is about the same as one full-width head.

## Masks

- **Padding mask**: positions that are padding receive a score of −∞ before the
  softmax, so they get zero weight.
- **Causal mask**: in a decoder, position i may attend only to positions ≤ i.
  The upper triangle of the score matrix is set to −∞. This is what allows a
  language model to be trained on every position of a sequence in one pass
  without seeing the future.

## Positional information

Attention is a set operation: permute the input tokens and the outputs are
permuted in exactly the same way. Word order has to be injected explicitly.

| scheme                | idea                                                  |
|-----------------------|-------------------------------------------------------|
| sinusoidal            | fixed sines and cosines of different frequencies added to the embeddings |
| learned absolute      | one trainable vector per position (BERT, GPT-2)       |
| relative              | a bias that depends on the distance i − j             |
| rotary (RoPE)         | rotate q and k by an angle proportional to position, so their dot product depends on relative offset |

Rotary embeddings are the common choice in current large language models.

## The rest of a block

A transformer layer is attention plus a position-wise feed-forward network,
each wrapped in a residual connection and layer normalisation:

```python
def block(x):
    # pre-norm ordering, as used by most modern models
    x = x + attention(norm1(x))
    x = x + mlp(norm2(x))      # mlp: Linear -> GELU -> Linear, 4x wider inside
    return x
```

The residual connections give gradients a direct route through dozens of layers.
The feed-forward network holds roughly two thirds of the parameters.

## Encoder, decoder, both

- **Encoder-only** (BERT): bidirectional self-attention. Suited to
  classification and to producing embeddings.
- **Decoder-only** (GPT): causal self-attention. Suited to generation.
- **Encoder–decoder** (the original model, T5): the decoder also has
  **cross-attention**, whose queries come from the decoder and whose keys and
  values come from the encoder output.

## Cost

Self-attention builds an n × n score matrix, so time and memory are
**O(n² · d)** in the sequence length n. Doubling the context quadruples the
attention cost. This is the main obstacle to very long contexts, and the
motivation for sparse and linear attention variants, for FlashAttention (the
same mathematics with far better memory locality), and for chunking long
documents before embedding them.

At inference time a decoder caches the keys and values of earlier tokens (the
**KV cache**), so producing each new token costs O(n) rather than O(n²).

## Training notes

- The original paper raised the learning rate linearly for the first 4000 steps
  and then decayed it in proportion to the inverse square root of the step
  number. Transformers are noticeably unstable without such a warmup phase,
  especially in the post-norm arrangement.
- Dropout is applied to the attention weights and to the output of each
  sub-layer; label smoothing of 0.1 was used as well.
- Pre-norm (normalise before each sub-layer) trains more stably at depth than
  the original post-norm.
