# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import torch
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer

from accelerate import PartialState
from accelerate.utils import gather_object


distributed_state = PartialState()

# You can change the model to any LLM such as mistralai/Mistral-7B-v0.1 or meta-llama/Llama-2-7b-chat-hf
model_name = "microsoft/phi-2"
model = AutoModelForCausalLM.from_pretrained(
    model_name, device_map=distributed_state.device, torch_dtype=torch.float16
)

tokenizer = AutoTokenizer.from_pretrained(model_name)
# needed for geenration
tokenizer.pad_token_id = tokenizer.eos_token_id

prompts = [
    "I would like to",
    "hello how are you",
    "what is going on",
    "roses are red and",
    "welcome to the hotel",
]

batch_size = 2
pad_to_multiple_of = 8

# split into batch
formatted_prompts = [
    prompts[i : i + batch_size] for i in range(0, len(prompts), batch_size)
]

# do the padding on the left since we are doing generation
padding_side_default = tokenizer.padding_side
tokenizer.padding_side = "left"
# tokenize each batch
tokenized_prompts = [
    tokenizer(formatted_prompt, padding=True, pad_to_multiple_of=pad_to_multiple_of, return_tensors="pt")
    for formatted_prompt in formatted_prompts
]
# put back padding
tokenizer.padding_side = padding_side_default

completions_per_process = []
with distributed_state.split_between_processes(tokenized_prompts, apply_padding=True) as batched_prompts:
    for batch in tqdm(batched_prompts, desc=f"Generating completions on device {distributed_state.device}"):
        # move the batch to the device
        batch = batch.to(distributed_state.device)
        outputs = model.generate(**batch, max_new_tokens=20)
        generated_text = tokenizer.batch_decode(outputs, skip_special_tokens=True)
        completions_per_process.extend(generated_text)

completions_gather = gather_object(completions_per_process)
# Drop duplicates produced by apply_padding in  split_between_processes
completions = completions_gather[: len(prompts)]

if distributed_state.is_main_process:
    print(completions)

