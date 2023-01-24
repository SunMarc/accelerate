import argparse

import torch

from accelerate.utils import convert_model, set_seed
from datasets import load_dataset
from modeling_bert import BertForSequenceClassification
from modeling_bert_te import BertForSequenceClassification as TEBertForSequenceClassification
from modeling_bert_te_lin import BertForSequenceClassification as TEBertForSequenceClassificationNoLN
from modeling_bert_te_ln import BertForSequenceClassification as TEBertForSequenceClassificationNoLinear
from transformers import AutoTokenizer


parser = argparse.ArgumentParser(description="Debugging conversion nn to te.")
parser.add_argument("--convert", action="store_true", help="Whether to convert or use the adapted models.")
parser.add_argument("--no_linear", action="store_true", help="Don't use te linear layers.")
parser.add_argument("--no_ln", action="store_true", help="Don't use te layernorm layers.")
args = parser.parse_args()

set_seed(0)

model = BertForSequenceClassification.from_pretrained("bert-base-cased").eval().to(0)
tokenizer = AutoTokenizer.from_pretrained("bert-base-cased")


dataset = load_dataset("glue", "mrpc")["train"].select(range(8))


def tokenize_function(examples):
    # max_length=None => use the model max length (it's actually the default)
    outputs = tokenizer(examples["sentence1"], examples["sentence2"], truncation=True, max_length=None)
    return outputs


tokenized_dataset = dataset.map(
    tokenize_function,
    batched=True,
    remove_columns=["idx", "sentence1", "sentence2"],
)


def collate_fn(examples):
    # On TPU it's best to pad everything to the same length or training will be very slow.
    return tokenizer.pad(examples, padding="longest", return_tensors="pt")


tokenized_dataset = tokenized_dataset.rename_column("label", "labels")
tokenized_dataset.set_format("torch")
batch = collate_fn(tokenized_dataset.to_dict()).to(0)
outputs = model(**batch, output_hidden_states=True)

state_dict = model.state_dict()

if args.convert:
    new_model = BertForSequenceClassification.from_pretrained("bert-base-cased").eval().to(0)
    with torch.no_grad():
        convert_model(new_model, _convert_linear=not args.no_linear, _convert_ln=not args.no_ln)
else:
    if args.no_linear and args.no_ln:
        model_cls = BertForSequenceClassification
    elif args.no_linear:
        model_cls = TEBertForSequenceClassificationNoLinear
    elif args.no_ln:
        model_cls = TEBertForSequenceClassificationNoLN
    else:
        model_cls = TEBertForSequenceClassification
    new_model = model_cls.from_pretrained("bert-base-cased").eval().to(0)

if not args.no_ln:
    state_dict = {k.replace("LayerNorm.", "LayerNorm.layer_norm_"): v for k, v in state_dict.items()}

new_model.load_state_dict(state_dict, strict=False)

# new_model.forward = fp8_autocast(enabled=False, fp8_recipe=DelayedScaling())(new_model.forward)
new_outputs = new_model(**batch, output_hidden_states=True)

print(f"Loss {outputs.loss} vs {new_outputs.loss}")

print("Loss comparison at 1e-6/1e-5/1e-4")
print(torch.allclose(outputs.loss, new_outputs.loss, atol=1e-6))
print(torch.allclose(outputs.loss, new_outputs.loss, atol=1e-5))
print(torch.allclose(outputs.loss, new_outputs.loss, atol=1e-4))

print(f"Logits {outputs.logits.tolist()} vs {new_outputs.logits.tolist()}")
print("Outputs comparison at 1e-6/1e-5/1e-4")
print(torch.allclose(outputs.logits, new_outputs.logits, atol=1e-6))
print(torch.allclose(outputs.logits, new_outputs.logits, atol=1e-5))
print(torch.allclose(outputs.logits, new_outputs.logits, atol=1e-4))

for i in range(len(outputs.hidden_states)):
    print(f"Hidden states {i} {outputs.hidden_states[i][:3,:2,:2].tolist()} vs {new_outputs.hidden_states[i][:3,:2,:2].tolist()}")
    print(torch.allclose(outputs.hidden_states[i], new_outputs.hidden_states[i], atol=1e-4))

outputs.loss.backward()
new_outputs.loss.backward()

grad1 = model.bert.embeddings.word_embeddings.weight.grad
grad2 = model.bert.embeddings.word_embeddings.weight.grad
print("Embeddings gradients at 1e-6/1e-5/1e-4")
print(torch.allclose(grad1, grad2, atol=1e-6))
print(torch.allclose(grad1, grad2, atol=1e-5))
print(torch.allclose(grad1, grad2, atol=1e-4))

grad1 = getattr(model.bert.encoder.layer, "0").attention.self.query.weight.grad
grad2 = getattr(new_model.bert.encoder.layer, "0").attention.self.query.weight.grad
print("Linear gradients at 1e-6/1e-5/1e-4")
print(torch.allclose(grad1, grad2, atol=1e-6))
print(torch.allclose(grad1, grad2, atol=1e-5))
print(torch.allclose(grad1, grad2, atol=1e-4))

grad1 = getattr(model.bert.encoder.layer, "0").attention.output.LayerNorm.weight.grad
if not args.no_ln:
    grad2 = getattr(new_model.bert.encoder.layer, "0").attention.output.LayerNorm.layer_norm_weight.grad
else:
    grad2 = getattr(new_model.bert.encoder.layer, "0").attention.output.LayerNorm.weight.grad
print("Layer norm gradients at 1e-6/1e-5/1e-4")
print(torch.allclose(grad1, grad2, atol=1e-6))
print(torch.allclose(grad1, grad2, atol=1e-5))
print(torch.allclose(grad1, grad2, atol=1e-4))
