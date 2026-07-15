---
name: 💡 Suggest a Code or Text Change
about: For non-coders to submit a quick fix or update using a free AI assistant.
title: "[Change Request]: "
labels: enhancement, documentation
assignees: ''

---

## 🛠 What needs to be changed?
<!-- Describe the change you want to see in plain English. (e.g., "Fix the typo on the pricing page", "Update the contact email address") -->


## 📄 AI-Generated Change Patch
We use a workflow that allows non-coders to generate the technical files we need using free, anonymous AI. Please follow these quick steps to generate your change patch:

1. **Open a Free AI Chat:** Open [Phind Chat](https://phind.com) or [HuggingChat](https://huggingface.co) in your browser. *(If using HuggingChat, remember to flip the **"Web Search"** toggle switch on).*
2. **Copy & Paste this prompt template into the AI:**
   ```text
   Go to this GitHub URL: [PASTE THE LINK TO THE TARGET FILE IN THIS REPO]

   I want to make the following change: 
   [DESCRIBE YOUR CHANGE HERE IN PLAIN ENGLISH]

   Please generate a raw, standard Unified Git Patch file (`diff --git`) block for this change so a developer can apply it using `git apply`. Only provide the raw patch code block.
   ```
3. **Paste the result below:** Click the "Copy" icon on the AI's code box output and paste it inside the triple backticks below.

```diff
<!-- PASTE YOUR AI OUTPUT HERE (Replace this line entirely) -->

```

Thank you for helping us improve! We will review this and submit a Pull Request on your behalf.

