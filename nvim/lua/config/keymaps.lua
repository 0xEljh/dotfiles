-- Keymaps are automatically loaded on the VeryLazy event
-- Default keymaps that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/keymaps.lua
-- Add any additional keymaps here

-- Swap j/k for up/down
vim.keymap.set({ "n", "v", "o" }, "j", "k", { desc = "Up" })
vim.keymap.set({ "n", "v", "o" }, "k", "j", { desc = "Down" })
vim.keymap.set({ "n", "v", "o" }, "gj", "gk", { desc = "Up (wrapped line)" })
vim.keymap.set({ "n", "v", "o" }, "gk", "gj", { desc = "Down (wrapped line)" })

-- Use fff.nvim for file and content search.
vim.keymap.del("n", "<leader>ff")
vim.keymap.del("n", "<leader>fF")

vim.keymap.set("n", "<leader>ff", function()
  require("fff").find_files_in_dir(vim.uv.cwd())
end, { desc = "Find Files (cwd)" })

vim.keymap.set("n", "<leader>fF", function()
  require("fff").find_files_in_dir(require("lazyvim.util").root.get())
end, { desc = "Find Files (Root Dir)" })

vim.keymap.set("n", "<leader>fg", function()
  require("fff").live_grep()
end, { desc = "Grep Files (FFF)" })

vim.keymap.set({ "n", "x" }, "<leader>fw", function()
  require("fff").live_grep_under_cursor()
end, { desc = "Grep Word/Selection (FFF)" })
