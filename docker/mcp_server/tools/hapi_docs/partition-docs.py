import os
import yaml
import multiprocessing
import time

# --- YAML Formatting Solution ---

# 1. A custom class to mark strings that should be dumped in the literal block style.
class LiteralString(str):
    pass

# 2. A custom representer function that tells PyYAML how to dump our LiteralString.
#    It will use the '|' style for multi-line blocks.
def literal_representer(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')

# 3. A helper function to recursively find multi-line strings in the loaded data
#    and wrap them in our custom LiteralString class.
def convert_multiline_strings(data):
    if isinstance(data, dict):
        return {k: convert_multiline_strings(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_multiline_strings(i) for i in data]
    if isinstance(data, str) and '\n' in data:
        return LiteralString(data)
    return data

# --- End of Formatting Solution ---


def create_spec_for_tag(args):
    """
    This is the worker function. It creates a spec file for a single tag,
    calculates its stats, and returns the stats to the main process.
    """
    # Unpack the arguments tuple
    tag_name, paths, base_spec, tag_definition, output_folder = args
    
    process_id = os.getpid()
    print(f"[Process {process_id}] Starting job for tag: '{tag_name}'")

    try:
        # Sanitize the tag name and create the full file path
        sanitized_tag = "".join(c for c in tag_name if c.isalnum() or c in (' ', '_')).replace(' ', '_')
        filepath = os.path.join(output_folder, f"{sanitized_tag}.yaml")

        # Create the spec dictionary for the current tag
        tag_spec = base_spec.copy()
        tag_spec["tags"] = [tag_definition] if tag_definition else []
        tag_spec["paths"] = paths
        
        # --- Register the custom representer in the worker process ---
        # This ensures the dumper knows how to handle our LiteralString class.
        yaml.add_representer(LiteralString, literal_representer)

        # Write the new spec to a YAML file, now with correct multi-line formatting
        with open(filepath, 'w', encoding='utf-8') as f:
            yaml.dump(tag_spec, f, sort_keys=False, indent=2, allow_unicode=True)

        # Re-open the file to calculate stats
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            line_count = len(content.splitlines())
            char_count = len(content)

        print(f"✅ [Process {process_id}] Created file for '{tag_name}' -> {filepath} [Lines: {line_count}, Chars: {char_count}]")
        
        return (True, line_count, char_count)

    except Exception as e:
        print(f"❌ [Process {process_id}] ERROR processing tag '{tag_name}': {e}")
        return (False, 0, 0)


def run_processing(input_file="hapi-api-docs.yaml", output_folder="api_specs_by_tag"):
    """
    Main function to orchestrate the reading, parsing, and parallel processing.
    """
    start_time = time.time()
    print("--- API Specification Splitter (Multiprocessing) ---")

    print(f"📁 Creating output directory if it doesn't exist: '{output_folder}'")
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    print(f"📖 Reading and parsing main spec file: '{input_file}'...")
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            full_spec = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"🚨 FATAL ERROR: Input file '{input_file}' not found.")
        return
    except yaml.YAMLError as e:
        print(f"🚨 FATAL ERROR: Could not parse YAML from '{input_file}'. Error: {e}")
        return
    
    # --- Apply the formatting fix after loading the data ---
    print("✨ Converting multi-line strings to preserve formatting...")
    full_spec = convert_multiline_strings(full_spec)

    print("📋 Preparing base specification and grouping endpoints by tag...")
    base_spec = {
        "openapi": full_spec.get("openapi"),
        "info": full_spec.get("info"),
        "servers": full_spec.get("servers"),
        "components": full_spec.get("components", {}),
    }

    paths_by_tag = {}
    paths_data = full_spec.get("paths", {})
    if paths_data:
        for path, path_item in paths_data.items():
            if path_item:
                for method, operation in path_item.items():
                    if operation and "tags" in operation:
                        for tag in operation["tags"]:
                            if tag not in paths_by_tag:
                                paths_by_tag[tag] = {}
                            if path not in paths_by_tag[tag]:
                                paths_by_tag[tag][path] = {}
                            paths_by_tag[tag][path][method] = operation

    if not paths_by_tag:
        print("⚠️ No tags found in the specification. Nothing to process.")
        return

    tag_definitions = {t.get("name"): t for t in full_spec.get("tags", [])}
    jobs = []
    for tag_name, paths in paths_by_tag.items():
        job_args = (tag_name, paths, base_spec, tag_definitions.get(tag_name), output_folder)
        jobs.append(job_args)

    print(f"👍 Setup complete. Found {len(jobs)} tags to process.")
    
    print("\n🚀 Starting parallel processing using a pool of workers...")
    
    with multiprocessing.Pool() as pool:
        results = pool.map(create_spec_for_tag, jobs)

    print("\n--- Processing Complete ---")
    
    successful_jobs = 0
    failed_jobs = 0
    total_lines = 0
    total_chars = 0

    for success, lines, chars in results:
        if success:
            successful_jobs += 1
            total_lines += lines
            total_chars += chars
        else:
            failed_jobs += 1
            
    print(f"📊 Summary:")
    print(f"   - Successfully generated: {successful_jobs} files")
    if failed_jobs > 0:
        print(f"   - Failed jobs: {failed_jobs}")

    print("\n📈 Global Statistics for Generated Files:")
    print(f"   - Total Lines:      {total_lines:,}")
    print(f"   - Total Characters: {total_chars:,}")
        
    end_time = time.time()
    print(f"\n⏱️ Total execution time: {end_time - start_time:.2f} seconds")


if __name__ == "__main__":
    run_processing()