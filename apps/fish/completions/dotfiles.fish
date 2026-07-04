# Completions for the `dotfiles` dispatcher (bin/dotfiles).
# Keep in sync with the case statement there.

complete -c dotfiles -f

complete -c dotfiles -n __fish_use_subcommand -a doctor -d "Run health check"
complete -c dotfiles -n __fish_use_subcommand -a uninstall -d "Remove \$HOME symlinks, restore backups"
complete -c dotfiles -n __fish_use_subcommand -a link -d "Refresh symlinks only (setup.bash --link-only)"
complete -c dotfiles -n __fish_use_subcommand -a setup -d "Run the full installer (setup.bash)"
complete -c dotfiles -n __fish_use_subcommand -a lint -d "Run all linters"
complete -c dotfiles -n __fish_use_subcommand -a help -d "Show usage"

complete -c dotfiles -n "__fish_seen_subcommand_from doctor" -l verbose -d "Print every passing check"
complete -c dotfiles -n "__fish_seen_subcommand_from doctor" -l quiet -d "Print only failures/skips (default)"

complete -c dotfiles -n "__fish_seen_subcommand_from uninstall" -l yes -d "Non-interactive; assume yes"
complete -c dotfiles -n "__fish_seen_subcommand_from uninstall" -s y -d "Non-interactive; assume yes"

complete -c dotfiles -n "__fish_seen_subcommand_from lint" -l ci -d "CI mode (fail on missing tools, no colors)"
complete -c dotfiles -n "__fish_seen_subcommand_from lint" -l fix -d "Auto-fix where possible"
