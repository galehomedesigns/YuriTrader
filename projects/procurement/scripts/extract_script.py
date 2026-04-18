import zipfile
import os
import xml.etree.ElementTree as ET

def extract_pptx_notes(pptx_path):
    notes = []
    try:
        with zipfile.ZipFile(pptx_path, 'r') as z:
            # Slides are usually in ppt/slides/slideN.xml
            # Notes are usually in ppt/notesSlides/notesSlideN.xml
            
            # First, find all notes slides
            note_files = [f for f in z.namelist() if f.startswith('ppt/notesSlides/notesSlide')]
            note_files.sort() # Try to keep some order
            
            for note_file in note_files:
                with z.open(note_file) as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    
                    # PPTX uses a lot of namespaces
                    namespaces = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
                    
                    # Find all text elements in the notes
                    slide_notes = []
                    for t in root.findall('.//a:t', namespaces):
                        if t.text:
                            slide_notes.append(t.text)
                    
                    if slide_notes:
                        notes.append("\n".join(slide_notes))
                        
        return "\n\n--- Slide Notes ---\n\n".join(notes)
    except Exception as e:
        return f"Error extracting notes: {e}"

if __name__ == "__main__":
    path = "/data/.openclaw/workspace/downloads/ProjectWheel_ScaleUP_Pitch_V5.2026.pptx"
    result = extract_pptx_notes(path)
    print(result)
