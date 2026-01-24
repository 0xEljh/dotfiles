return {
  {
    "3rd/image.nvim",
    opts = {
      backend = "kitty",
      integrations = {
        markdown = { enabled = false },
      },
      max_width = 100,
      max_height = 12,
      max_height_window_percentage = math.huge,
      max_width_window_percentage = math.huge,
      window_overlap_clear_enabled = true,
      window_overlap_clear_ft_ignore = { "cmp_menu", "cmp_docs", "" },
    },
  },
  {
    "benlubas/molten-nvim",
    version = "^1.0.0",
    dependencies = { "3rd/image.nvim" },
    build = ":UpdateRemotePlugins",
    init = function()
      vim.g.molten_image_provider = "image.nvim"
      vim.g.molten_output_win_max_height = 20
      vim.g.molten_auto_open_output = false
      vim.g.molten_virt_text_output = true
      vim.g.molten_virt_lines_off_by_1 = true
    end,
    keys = {
      { "<leader>mi", "<cmd>MoltenInit<cr>", desc = "Molten Init" },
      { "<leader>me", "<cmd>MoltenEvaluateOperator<cr>", desc = "Molten Evaluate Operator" },
      { "<leader>ml", "<cmd>MoltenEvaluateLine<cr>", desc = "Molten Evaluate Line" },
      { "<leader>mr", "<cmd>MoltenReevaluateCell<cr>", desc = "Molten Re-evaluate Cell" },
      { "<leader>md", "<cmd>MoltenDelete<cr>", desc = "Molten Delete Cell" },
      { "<leader>mo", "<cmd>MoltenShowOutput<cr>", desc = "Molten Show Output" },
      { "<leader>mh", "<cmd>MoltenHideOutput<cr>", desc = "Molten Hide Output" },
      { "<leader>mx", "<cmd>MoltenInterrupt<cr>", desc = "Molten Interrupt" },
      { "<leader>m", ":<C-u>MoltenEvaluateVisual<cr>gv", mode = "v", desc = "Molten Evaluate Visual" },
    },
  },
  {
    "GCBallesteros/jupytext.nvim",
    config = true,
    lazy = false,
  },
}
