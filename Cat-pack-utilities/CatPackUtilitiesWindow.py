import glfw
from OpenGL.GL import *
import ctypes
import logging

from OpenGL.GL import shaders
from PIL import Image

# Configure logging
logging.basicConfig(level=logging.INFO)


def window_resize(window, width, height):
    glViewport(0, 0, width, height)


def load_texture(file_path):
    image = Image.open(file_path).transpose(Image.FLIP_TOP_BOTTOM)
    img_data = image.tobytes("raw", "RGBA", 0, -1)

    texture = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, texture)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, image.width, image.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, img_data)
    glBindTexture(GL_TEXTURE_2D, 0)

    return texture


def main():
    # Initialize GLFW
    if not glfw.init():
        logging.error("GLFW initialization failed")
        return

    # Create a windowed mode window and its OpenGL context
    window = glfw.create_window(1280, 720, "Cat Pack Utilities", None, None)
    if not window:
        logging.error("Window or OpenGL context creation failed")
        glfw.terminate()
        return

    # Set callbacks
    glfw.set_window_size_callback(window, window_resize)

    # Load the PNG image as the window icon
    icon_image = Image.open("cat.png")
    glfw.set_window_icon(window, 1, [icon_image])

    # Make the window's context current
    glfw.make_context_current(window)

    # Define the vertices of a rectangle to display the texture as an icon
    icon_vertices = [-0.5, -0.5, 0.0,  # Bottom-left corner
                     0.5, -0.5, 0.0,  # Bottom-right corner
                     0.5, 0.5, 0.0,  # Top-right corner
                     -0.5, 0.5, 0.0]  # Top-left corner

    icon_vertices = (GLfloat * len(icon_vertices))(*icon_vertices)

    # Vertex shader source code
    vertex_shader_src = """
    #version 330 core
    layout (location = 0) in vec3 aPos;
    void main()
    {
        gl_Position = vec4(aPos.x, aPos.y, aPos.z, 1.0);
    }
    """

    # Fragment shader source code
    fragment_shader_src = """
    #version 330 core
    out vec4 FragColor;
    uniform sampler2D textureSampler;
    void main()
    {
        FragColor = texture(textureSampler, vec2(gl_FragCoord.x / 1280.0, gl_FragCoord.y / 720.0));
    }
    """

    # Compile shaders and link program
    shader = shaders.compileProgram(
        shaders.compileShader(vertex_shader_src, GL_VERTEX_SHADER),
        shaders.compileShader(fragment_shader_src, GL_FRAGMENT_SHADER)
    )
    glUseProgram(shader)

    # Create Vertex Array Object (VAO) and Vertex Buffer Object (VBO) for the icon rectangle
    vao = glGenVertexArrays(1)
    vbo = glGenBuffers(1)
    glBindVertexArray(vao)
    glBindBuffer(GL_ARRAY_BUFFER, vbo)
    glBufferData(GL_ARRAY_BUFFER, len(icon_vertices) * ctypes.sizeof(GLfloat), icon_vertices, GL_STATIC_DRAW)
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 3 * ctypes.sizeof(GLfloat), ctypes.c_void_p(0))
    glEnableVertexAttribArray(0)
    glBindBuffer(GL_ARRAY_BUFFER, 0)
    glBindVertexArray(0)

    # Main loop
    while not glfw.window_should_close(window):
        glClear(GL_COLOR_BUFFER_BIT)

        glUseProgram(shader)
        glBindVertexArray(vao)
        glActiveTexture(GL_TEXTURE0)

        glUniform1i(glGetUniformLocation(shader, "textureSampler"), 0)
        glDrawArrays(GL_QUADS, 0, 4)

        glfw.swap_buffers(window)
        glfw.poll_events()

    # Terminate GLFW and cleanup
    logging.info("Closing the application")
    glfw.terminate()


if __name__ == "__main__":
    main()