#!/bin/bash

script_name="$(basename "$0")" # name of the shell file, so that we can ignore it later (since it has TODO in it)
current_dir=$(basename "$dir") # name of the current directory
dir=${1:-"."}                  # directory to search for TODOs is in the first argument, default is the current directory

# function to process each file
process_file() {
    local file="$1"
    local line_num=1
    while IFS= read -r line; do
        if [[ "$line" == *"TODO: "* ]]; then
            todo_text=$(echo "$line" | sed -E 's/^.*TODO: //')
            # echo -e "  \033[0;36mLine $line_num: \033[1;37m$todo_text\033[0m" # bold text
            echo -e "  \033[0;36mLine $line_num: \033[0;37m$todo_text\033[0m"
        fi
        ((line_num++))
    done < "$file"
}

# recursive function to process directories
process_directory() {
    local dir="$1"
    for entry in "$dir"/*; do
        if [[ -d "$entry" ]]; then
            process_directory "$entry"
        elif [[ -f "$entry" && "$(basename "$entry")" != "$script_name" ]]; then
            if grep -q "TODO: " "$entry"; then
                echo -e "\033[1;32m📁 Processing file: \033[1;33m$entry\033[0m"
                echo -e "\033[0;34m--------------------------------\033[0m"
                process_file "$entry"
                echo
            fi
        fi
    done
}

echo -e "\n\033[1;34m==================================\033[0m"

# if no directory is provided, print the cwd
if [[ "$dir" == "." ]]; then
    current_dir=$(basename "$(pwd)")
    echo -e "\033[1;32m🔍 Searching for TODOs in the current directory '\033[1;33m$current_dir/\033[1;32m' and its subdirectories...\033[0m"
else
    echo -e "\033[1;32m🔍 Searching for TODOs in directory '\033[1;33m$dir/\033[1;32m' and its subdirectories...\033[0m"
fi

echo
process_directory "$dir"
echo -e "\033[1;35m✅ Search complete. Get to work!\033[0m"