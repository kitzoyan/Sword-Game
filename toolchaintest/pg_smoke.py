import os, pygame
pygame.init()
screen = pygame.display.set_mode((320, 240))
pygame.display.set_caption("smoke")
screen.fill((92, 148, 252))           # mario sky blue
pygame.draw.rect(screen, (228,92,16), (40,160,60,40))
pygame.display.flip()
pygame.event.pump()
pygame.time.wait(300)
print("display ok:", pygame.display.get_surface().get_size())
pygame.quit()
