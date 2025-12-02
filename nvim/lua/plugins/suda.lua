return {
  {
    "lambdalisue/suda.vim",
    init = function()
      -- This setting tells the plugin to ask for your password automatically
      -- if you try to save a file you don't own.
      vim.g.suda_smart_edit = 1
    end,
  },
}
