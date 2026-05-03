# Replace Text Task Implementation

"""
Task ReplaceText - Replace text in content.
"""

import re
from typing import Dict, Any, List
from core import FlowFile, TaskError
from core.base_task import BaseTask


class ReplaceTextTask(BaseTask):
    """
    Task for replacing text in FlowFile content.
    
    Supports plain text or regex replacement.
    """
    
    TYPE = "replace_text"
    VERSION = "1.0.0"
    NAME = "Remplacer du texte"
    DESCRIPTION = "Replace text in content"
    ICON = "replace"
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the ReplaceText task.
        
        Args:
            config: Configuration avec:
                - search_pattern: Search pattern (requis)
                - replacement: Replacement text (requis)
                - regex: Use regex (default: false)
                - case_sensitive: Case sensitive (default: true)
                - multiline: Multiline (default: false)
        """
        super().__init__(config)
        
        self.search_pattern = self.config.get('search_pattern', '')
        self.replacement = self.config.get('replacement', '')
        self.use_regex = self.config.get('regex', False)
        self.case_sensitive = self.config.get('case_sensitive', True)
        self.multiline = self.config.get('multiline', False)
        
        # Compile the regex expression if needed
        self._compile_pattern()
    
    def _compile_pattern(self):
        """Compile the regex expression if needed."""
        if self.use_regex:
            flags = 0
            if not self.case_sensitive:
                flags |= re.IGNORECASE
            if self.multiline:
                flags |= re.MULTILINE
            
            try:
                self.pattern = re.compile(self.search_pattern, flags)
            except re.error as e:
                raise TaskError(f"Motif regex invalide: {e}")
        else:
            self.pattern = None
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Execute the ReplaceText task.
        
        Args:
            flowfile: Input FlowFile
            
        Returns:
            List containing the modified FlowFile
            
        Raises:
            TaskError: If the task fails
        """
        try:
            # Read content
            content = self.read_content(flowfile)
            
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            
            # Resolve expression language in replacement (e.g. ${http.path.who})
            from core.expression import resolve_expression
            resolved_replacement = resolve_expression(
                self.replacement,
                attributes=flowfile.get_attributes(),
                parameters=getattr(self, '_parameter_context', None),
            )

            # Perform the replacement
            if self.use_regex and self.pattern:
                new_content = self.pattern.sub(resolved_replacement, content)
            else:
                # Plain string replacement
                flags = 0 if self.case_sensitive else re.IGNORECASE
                # Escape the pattern if it is not a regex
                escaped_pattern = re.escape(self.search_pattern)
                new_content = re.sub(escaped_pattern, resolved_replacement, content, flags=flags)
            
            # Write the modified content
            if isinstance(new_content, str):
                new_content = new_content.encode('utf-8')
            
            self.write_content(flowfile, new_content)
            
            # Update the size attribute
            flowfile.set_attribute('fileSize', str(len(new_content)))
            
            return [flowfile]
        
        except UnicodeDecodeError as e:
            raise TaskError(f"Erreur de décodage du contenu: {e}")
        except Exception as e:
            raise TaskError(f"Erreur lors du remplacement: {e}")
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Return the parameter schema.
        
        Returns:
            Parameter schema for the UI
        """
        return {
            'search_pattern': {
                'type': 'string',
                'required': True,
                'description': 'Search pattern',
                'placeholder': 'pattern to search for'
            },
            'replacement': {
                'type': 'string',
                'required': True,
                'description': 'Replacement text',
                'placeholder': 'nouveau texte'
            },
            'regex': {
                'type': 'boolean',
                'required': False,
                'description': 'Use a regular expression',
                'default': False
            },
            'case_sensitive': {
                'type': 'boolean',
                'required': False,
                'description': 'Case sensitive',
                'default': True
            },
            'multiline': {
                'type': 'boolean',
                'required': False,
                'description': 'Mode multi-lignes (pour regex uniquement)',
                'default': False
            }
        }