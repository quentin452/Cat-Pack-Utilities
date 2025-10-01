import os
import re
import glob
import zipfile
from collections import defaultdict, Counter
import logging
from datetime import datetime

def setup_logging():
    """Configure logging for the excessive calls finder"""
    log_folder = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'excessive_calls_logs')
    os.makedirs(log_folder, exist_ok=True)
    log_file = os.path.join(log_folder, f'excessive_calls_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    return log_file

def get_file_extensions():
    """Return supported file extensions for code analysis"""
    return {
        'java': ['.java'],
        'javascript': ['.js', '.jsx', '.ts', '.tsx'],
        'python': ['.py'],
        'cpp': ['.cpp', '.cc', '.cxx', '.c', '.h', '.hpp'],
        'csharp': ['.cs'],
        'go': ['.go'],
        'rust': ['.rs'],
        'php': ['.php'],
        'ruby': ['.rb'],
        'kotlin': ['.kt', '.kts'],
        'scala': ['.scala'],
        'swift': ['.swift'],
        'jar': ['.jar'], 
        'class': ['.class'] 
    }

def analyze_class_file(class_path, method_name, patterns):
    """Analyze a .class file for method calls using bytecode analysis"""
    results = []
    
    try:
        with open(class_path, 'rb') as f:
            bytecode = f.read()
        
        # Convert bytecode to string for pattern matching
        bytecode_str = str(bytecode, errors='ignore')
        
        # Look for method signatures and invocations in bytecode
        # Java bytecode contains method names in the constant pool
        method_patterns = [
            # Method invocation patterns in bytecode
            rf'{re.escape(method_name)}',  # Basic method name
            rf'invoke.*{re.escape(method_name)}',  # invokevirtual, invokestatic, etc.
            rf'Method.*{re.escape(method_name)}',  # Method references
        ]
        
        for i, pattern in enumerate(method_patterns):
            regex = re.compile(pattern, re.IGNORECASE)
            matches = list(regex.finditer(bytecode_str))
            
            for match in matches:
                # Find surrounding context
                start_pos = max(0, match.start() - 50)
                end_pos = min(len(bytecode_str), match.end() + 50)
                context = bytecode_str[start_pos:end_pos]
                
                # Clean up the context for display
                context_clean = ''.join(c if c.isprintable() else '.' for c in context)
                
                results.append({
                    'line_number': i + 1,  # Approximate line number
                    'line_content': f"[BYTECODE] {context_clean[:100]}",
                    'call_context': f"Bytecode pattern match in {os.path.basename(class_path)}",
                    'match_start': match.start(),
                    'match_end': match.end(),
                    'matched_text': match.group(),
                    'class_file': class_path
                })
                
    except Exception as e:
        logging.warning(f"Could not analyze .class file {class_path}: {e}")
    
    return results

def analyze_jar_file(jar_path, method_name, patterns):
    """Analyze a JAR file for method calls by examining source files within it"""
    results = []
    
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar:
            # Look for source files within the JAR
            source_files = []
            for name in jar.namelist():
                if (name.endswith('.java') or name.endswith('.kt') or 
                    name.endswith('.scala') or name.endswith('.groovy')):
                    source_files.append(name)
            
            if not source_files:
                # If no source files, analyze class files with improved bytecode analysis
                class_files = [name for name in jar.namelist() if name.endswith('.class')]
                if class_files:
                    for class_file in class_files[:100]:  # Limit to avoid excessive processing
                        try:
                            with jar.open(class_file) as f:
                                bytecode = f.read()
                            
                            # Enhanced bytecode analysis
                            bytecode_str = str(bytecode, errors='ignore')
                            
                            # Look for method invocation patterns
                            method_patterns = [
                                rf'{re.escape(method_name)}',  # Basic method name
                                rf'invoke.*{re.escape(method_name)}',  # Method invocations
                                rf'Method.*{re.escape(method_name)}',  # Method references
                            ]
                            
                            for pattern in method_patterns:
                                regex = re.compile(pattern, re.IGNORECASE)
                                matches = list(regex.finditer(bytecode_str))
                                
                                for match in matches:
                                    # Get context around the match
                                    start_pos = max(0, match.start() - 30)
                                    end_pos = min(len(bytecode_str), match.end() + 30)
                                    context = bytecode_str[start_pos:end_pos]
                                    
                                    # Clean context for display
                                    context_clean = ''.join(c if c.isprintable() else '.' for c in context)
                                    
                                    results.append({
                                        'line_number': 1,  # Bytecode doesn't have traditional line numbers
                                        'line_content': f"[BYTECODE] {context_clean[:80]}",
                                        'call_context': f"Bytecode analysis in {class_file}",
                                        'match_start': match.start(),
                                        'match_end': match.end(),
                                        'matched_text': match.group(),
                                        'source_file': class_file,
                                        'jar_file': jar_path
                                    })
                        except Exception as e:
                            logging.debug(f"Could not analyze {class_file} in {jar_path}: {e}")
                            continue
                return results
            
            # Analyze source files within the JAR
            for source_file in source_files:
                try:
                    with jar.open(source_file) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        lines = content.split('\n')
                        
                        for pattern in patterns:
                            regex = re.compile(pattern, re.IGNORECASE)
                            
                            for line_num, line in enumerate(lines, 1):
                                matches = regex.finditer(line)
                                for match in matches:
                                    call_context = line.strip()
                                    
                                    if len(call_context) < 50 and line_num < len(lines):
                                        start_line = max(0, line_num - 2)
                                        end_line = min(len(lines), line_num + 1)
                                        call_context = ' '.join(lines[start_line:end_line]).strip()
                                    
                                    results.append({
                                        'line_number': line_num,
                                        'line_content': line.strip(),
                                        'call_context': call_context,
                                        'match_start': match.start(),
                                        'match_end': match.end(),
                                        'matched_text': match.group(),
                                        'source_file': source_file,
                                        'jar_file': jar_path
                                    })
                except Exception as e:
                    logging.debug(f"Could not analyze {source_file} in {jar_path}: {e}")
                    continue
                    
    except Exception as e:
        logging.warning(f"Could not analyze JAR file {jar_path}: {e}")
    
    return results

def create_method_patterns(method_name):
    """Create regex patterns to match different method call scenarios"""
    patterns = []
    
    # Direct method calls: methodName(
    patterns.append(rf'\b{re.escape(method_name)}\s*\(')
    
    # Instance method calls: instance.methodName(
    patterns.append(rf'\w+\s*\.\s*{re.escape(method_name)}\s*\(')
    
    # Static method calls: Class.methodName(
    patterns.append(rf'[A-Z]\w*\s*\.\s*{re.escape(method_name)}\s*\(')
    
    # Chain calls: something.something.methodName(
    patterns.append(rf'\w+(?:\s*\.\s*\w+)*\s*\.\s*{re.escape(method_name)}\s*\(')
    
    return patterns

def analyze_file_for_calls(file_path, method_name, patterns):
    """Analyze a single file for method calls"""
    # Handle JAR files specially
    if file_path.lower().endswith('.jar'):
        return analyze_jar_file(file_path, method_name, patterns)
    
    # Handle .class files specially
    if file_path.lower().endswith('.class'):
        return analyze_class_file(file_path, method_name, patterns)
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        results = []
        lines = content.split('\n')
        
        for pattern in patterns:
            regex = re.compile(pattern, re.IGNORECASE)
            
            for line_num, line in enumerate(lines, 1):
                matches = regex.finditer(line)
                for match in matches:
                    # Extract the full call context
                    call_context = line.strip()
                    
                    # Try to get more context if the line is short
                    if len(call_context) < 50 and line_num < len(lines):
                        start_line = max(0, line_num - 2)
                        end_line = min(len(lines), line_num + 1)
                        call_context = ' '.join(lines[start_line:end_line]).strip()
                    
                    results.append({
                        'line_number': line_num,
                        'line_content': line.strip(),
                        'call_context': call_context,
                        'match_start': match.start(),
                        'match_end': match.end(),
                        'matched_text': match.group()
                    })
                    
        return results
        
    except Exception as e:
        logging.warning(f"Could not analyze file {file_path}: {e}")
        return []

def find_files_to_analyze(search_path, file_extensions):
    """Find all files to analyze based on extensions"""
    files_to_analyze = []
    
    for ext_group, extensions in file_extensions.items():
        for ext in extensions:
            pattern = os.path.join(search_path, f"**/*{ext}")
            files_to_analyze.extend(glob.glob(pattern, recursive=True))
    
    return files_to_analyze

def calculate_call_statistics(all_results):
    """Calculate statistics about method calls"""
    stats = {
        'total_calls': 0,
        'files_with_calls': 0,
        'calls_per_file': defaultdict(int),
        'calls_per_line_type': defaultdict(int),
        'most_common_contexts': Counter()
    }
    
    files_with_calls = set()
    
    for file_path, calls in all_results.items():
        if calls:
            files_with_calls.add(file_path)
            stats['calls_per_file'][file_path] = len(calls)
            stats['total_calls'] += len(calls)
            
            for call in calls:
                # Analyze the type of call
                matched_text = call['matched_text'].lower()
                if '.' in matched_text:
                    stats['calls_per_line_type']['instance_or_static'] += 1
                else:
                    stats['calls_per_line_type']['direct'] += 1
                
                # Track common contexts
                context_key = call['call_context'][:100]  # First 100 chars
                stats['most_common_contexts'][context_key] += 1
    
    stats['files_with_calls'] = len(files_with_calls)
    return stats

def detect_excessive_usage(stats, threshold=10):
    """Detect potentially excessive usage patterns"""
    issues = []
    
    # Files with too many calls
    for file_path, call_count in stats['calls_per_file'].items():
        if call_count >= threshold:
            issues.append({
                'type': 'excessive_file_usage',
                'file': file_path,
                'count': call_count,
                'severity': 'high' if call_count >= threshold * 2 else 'medium'
            })
    
    # Check for suspicious patterns in contexts
    for context, count in stats['most_common_contexts'].most_common(10):
        if count >= 5:  # Same context repeated many times
            issues.append({
                'type': 'repeated_context',
                'context': context,
                'count': count,
                'severity': 'medium' if count >= 10 else 'low'
            })
    
    return issues

def generate_report(method_name, all_results, stats, issues, output_file):
    """Generate a detailed report"""
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"=== EXCESSIVE CALLS ANALYSIS REPORT ===\n")
        f.write(f"Method searched: {method_name}\n")
        f.write(f"Analysis date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"=" * 50 + "\n\n")
        
        # Summary statistics
        f.write("📊 SUMMARY STATISTICS\n")
        f.write(f"Total calls found: {stats['total_calls']}\n")
        f.write(f"Files with calls: {stats['files_with_calls']}\n")
        f.write(f"Average calls per file: {stats['total_calls'] / max(1, stats['files_with_calls']):.2f}\n\n")
        
        # Call types breakdown
        f.write("📈 CALL TYPES BREAKDOWN\n")
        for call_type, count in stats['calls_per_line_type'].items():
            percentage = (count / stats['total_calls'] * 100) if stats['total_calls'] > 0 else 0
            f.write(f"{call_type}: {count} ({percentage:.1f}%)\n")
        f.write("\n")
        
        # Issues detected
        if issues:
            f.write("🚨 POTENTIAL ISSUES DETECTED\n")
            for issue in issues:
                if issue['type'] == 'excessive_file_usage':
                    f.write(f"⚠️  {issue['severity'].upper()}: {issue['file']}\n")
                    f.write(f"    {issue['count']} calls found (threshold: 10)\n\n")
                elif issue['type'] == 'repeated_context':
                    f.write(f"🔄 REPEATED PATTERN ({issue['severity']}):\n")
                    f.write(f"    Context: {issue['context'][:80]}...\n")
                    f.write(f"    Occurrences: {issue['count']}\n\n")
        else:
            f.write("✅ NO EXCESSIVE USAGE DETECTED\n\n")
        
        # Detailed file analysis
        f.write("📁 DETAILED FILE ANALYSIS\n")
        sorted_files = sorted(stats['calls_per_file'].items(), key=lambda x: x[1], reverse=True)
        
        for file_path, call_count in sorted_files[:20]:  # Top 20 files
            is_jar = file_path.lower().endswith('.jar')
            is_class = file_path.lower().endswith('.class')
            
            if is_jar:
                file_type = "JAR FILE"
            elif is_class:
                file_type = "CLASS FILE"
            else:
                file_type = "SOURCE FILE"
                
            f.write(f"\n📄 {file_type}: {file_path} ({call_count} calls)\n")
            f.write("-" * 60 + "\n")
            
            calls = all_results[file_path]
            for call in calls[:10]:  # First 10 calls per file
                if is_jar and 'source_file' in call:
                    f.write(f"In {call['source_file']}, Line {call['line_number']}: {call['line_content']}\n")
                elif is_class and 'class_file' in call:
                    f.write(f"Bytecode analysis: {call['line_content']}\n")
                else:
                    f.write(f"Line {call['line_number']}: {call['line_content']}\n")
            
            if len(calls) > 10:
                f.write(f"... and {len(calls) - 10} more calls\n")
        
        # Most common contexts
        f.write(f"\n🔍 MOST COMMON CALL CONTEXTS\n")
        for context, count in stats['most_common_contexts'].most_common(15):
            f.write(f"{count}x: {context[:100]}{'...' if len(context) > 100 else ''}\n")

def print_console_summary(method_name, stats, issues):
    """Print a summary to console"""
    print(f"\n🎯 EXCESSIVE CALLS ANALYSIS - {method_name}")
    print("=" * 50)
    print(f"📊 Total calls found: {stats['total_calls']}")
    print(f"📁 Files with calls: {stats['files_with_calls']}")
    
    if stats['total_calls'] > 0:
        avg_calls = stats['total_calls'] / stats['files_with_calls']
        print(f"📈 Average calls per file: {avg_calls:.2f}")
        
        # Show top files
        sorted_files = sorted(stats['calls_per_file'].items(), key=lambda x: x[1], reverse=True)
        print(f"\n🏆 TOP FILES WITH MOST CALLS:")
        for file_path, count in sorted_files[:5]:
            filename = os.path.basename(file_path)
            print(f"   {count:3d} calls - {filename}")
        
        # Show issues
        if issues:
            print(f"\n🚨 ISSUES DETECTED:")
            high_severity = [i for i in issues if i.get('severity') == 'high']
            medium_severity = [i for i in issues if i.get('severity') == 'medium']
            
            if high_severity:
                print(f"   🔴 High severity: {len(high_severity)} issues")
            if medium_severity:
                print(f"   🟡 Medium severity: {len(medium_severity)} issues")
        else:
            print(f"\n✅ No excessive usage patterns detected")
    else:
        print(f"❌ No calls found for method '{method_name}'")

def main():
    """Main function for interactive usage"""
    log_file = setup_logging()
    
    print("=== CAT PACK EXCESSIVE CALLS FINDER ===")
    print("Search for potentially excessive method calls in your codebase")
    print("\n💡 TIP: Use only the method name (e.g., 'atan2' instead of 'Math.atan2')")
    print("This tool will automatically detect various call patterns:\n")
    print("  ✓ Direct calls: methodName()")
    print("  ✓ Instance calls: object.methodName()")
    print("  ✓ Static calls: Class.methodName()")
    print("  ✓ Chained calls: obj.prop.methodName()")
    print("  ✓ JAR files: Analyzes source files within JAR archives\n")
    print("🔍 SUPPORTED FILE TYPES:")
    print("  • Source files: .java, .py, .js, .ts, .cpp, .cs, .go, .rs, etc.")
    print("  • JAR files: .jar (analyzes embedded source files or bytecode)")
    print("  • Class files: .class (Java bytecode analysis)\n")
    
    while True:
        search_path = input("Enter the path to search (or 'exit' to quit): ").strip()
        
        if search_path.lower() == 'exit':
            break
            
        if not os.path.isdir(search_path):
            print("❌ Directory not found. Please try again.")
            continue
            
        method_name = input("Enter the method name to search for (e.g., 'atan2'): ").strip()
        
        if not method_name:
            print("❌ Method name cannot be empty.")
            continue
        
        # Optional threshold
        threshold_input = input("Enter call threshold for 'excessive' detection (default: 10): ").strip()
        try:
            threshold = int(threshold_input) if threshold_input else 10
        except ValueError:
            threshold = 10
        
        print(f"\n🔍 Searching for '{method_name}' calls in {search_path}...")
        print("⏳ This may take a moment for large codebases...")
        
        try:
            # Find files to analyze
            file_extensions = get_file_extensions()
            files_to_analyze = find_files_to_analyze(search_path, file_extensions)
            
            if not files_to_analyze:
                print("❌ No supported code files found in the specified directory.")
                print("📝 Supported types: .java, .py, .js, .ts, .cpp, .cs, .go, .rs, .jar, .class, etc.")
                print("💡 JAR files: Analyzes embedded source files or bytecode")
                print("💡 .class files: Performs bytecode analysis for method calls")
                continue
            
            # Count different file types
            jar_files = [f for f in files_to_analyze if f.lower().endswith('.jar')]
            class_files = [f for f in files_to_analyze if f.lower().endswith('.class')]
            source_files = [f for f in files_to_analyze if not f.lower().endswith('.jar') and not f.lower().endswith('.class')]
            
            print(f"📂 Analyzing {len(files_to_analyze)} files...")
            if jar_files:
                print(f"   🗃️  {len(jar_files)} JAR files")
            if class_files:
                print(f"   🔧 {len(class_files)} .class files")
            if source_files:
                print(f"   📄 {len(source_files)} source files")
            
            # Create search patterns
            patterns = create_method_patterns(method_name)
            
            # Analyze all files
            all_results = {}
            analyzed_count = 0
            
            for file_path in files_to_analyze:
                results = analyze_file_for_calls(file_path, method_name, patterns)
                if results:
                    all_results[file_path] = results
                analyzed_count += 1
                
                if analyzed_count % 100 == 0:
                    print(f"   📊 Analyzed {analyzed_count}/{len(files_to_analyze)} files...")
            
            # Calculate statistics
            stats = calculate_call_statistics(all_results)
            
            # Detect issues
            issues = detect_excessive_usage(stats, threshold)
            
            # Generate report
            report_folder = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'excessive_calls_reports')
            os.makedirs(report_folder, exist_ok=True)
            report_file = os.path.join(report_folder, f'excessive_calls_{method_name}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
            
            generate_report(method_name, all_results, stats, issues, report_file)
            
            # Print console summary
            print_console_summary(method_name, stats, issues)
            
            print(f"\n📄 Detailed report saved: {report_file}")
            
        except Exception as e:
            logging.error(f"Error during analysis: {e}")
            print(f"❌ Error: {e}")
        
        choice = input("\nWould you like to search for another method? (y/n): ").lower()
        if choice != 'y':
            break
    
    print("\n👋 Analysis complete!")

if __name__ == "__main__":
    main()