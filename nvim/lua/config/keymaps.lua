-- Keymaps are automatically loaded on the VeryLazy event
-- Default keymaps that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/keymaps.lua
-- Add any additional keymaps here

-- Swap keymaps for telescope's find files (cwd vs root dir)
vim.keymap.del("n", "<leader>ff")
vim.keymap.del("n", "<leader>fF")

vim.keymap.set("n", "<leader>ff", "<cmd>Telescope find_files cwd=.<cr>", { desc = "Find Files (cwd)" })
vim.keymap.set("n", "<leader>fF", function()
  require("lazyvim.util").telescope("files", { cwd = false })()
end, { desc = "Find Files (Root Dir)" })
